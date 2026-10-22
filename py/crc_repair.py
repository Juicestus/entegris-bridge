"""
Single bit repair of a pump frame that failed its CRC. The bus flips the odd bit, and one flipped bit can be found
exactly because the CRC is linear. A repair is only a candidate, the caller decides whether to trust it.

The pull repairs live under its own checks. Run on its own, this looks at a saved .bad file:

    python crc_repair.py <bad file> [--write-reconstruction PATH]
"""

import argparse
import os
import sys

from crc16 import BYTE_MASK, CRC_LEN, RESIDUE, TABLE, crc16

BITS_PER_BYTE = 8
FRAME_HEADER_LEN = 4
WORD_LEN = 2

NOT_PUMP_DATA = "offline diagnosis only, a repaired frame is a reconstruction, not pump data"


def flip_effects(length):
    """
    For every bit of a frame this long, how flipping it changes the CRC, as {(byte, bit): change}.
    """
    # the CRC is linear, so a flipped bit changes the result by the CRC of that bit alone followed by zero bytes,
    # whatever the rest of the frame holds. walking back from the last byte adds one zero byte per step
    effects = {}
    states = [TABLE[1 << bit] for bit in range(BITS_PER_BYTE)]
    for offset in range(length - 1, -1, -1):
        for bit in range(BITS_PER_BYTE):
            effects[(offset, bit)] = states[bit]
        states = [TABLE[state & BYTE_MASK] ^ (state >> BITS_PER_BYTE) for state in states]
    return effects


def repairing_bits(frame):
    """
    Every (byte, bit) whose flip alone makes the frame pass its CRC check.
    """
    needed = crc16(frame) ^ RESIDUE
    return [position for position, change in flip_effects(len(frame)).items() if change == needed]


def flip(frame, offset, bit):
    repaired = bytearray(frame)
    repaired[offset] ^= 1 << bit
    return bytes(repaired)


def where(offset, length):
    """
    Which part of the frame a byte offset falls in.
    """
    if offset < FRAME_HEADER_LEN:
        return "header"
    if offset >= length - CRC_LEN:
        return "CRC"
    payload = offset - FRAME_HEADER_LEN
    half = "low" if payload % WORD_LEN == 0 else "high"
    return f"payload word {payload // WORD_LEN}, {half} byte"


def main():
    parser = argparse.ArgumentParser(description=f"Single bit CRC diagnosis of a .bad file. {NOT_PUMP_DATA}.")
    parser.add_argument("bad_file")
    parser.add_argument("--write-reconstruction", metavar="PATH",
                        help=f"write the frame with the one repairing bit flipped. {NOT_PUMP_DATA}")
    args = parser.parse_args()

    with open(args.bad_file, "rb") as file:
        frame = file.read()
    print(f"  {args.bad_file}, {len(frame)} bytes")
    print(f"  {NOT_PUMP_DATA}")

    if len(frame) < FRAME_HEADER_LEN + CRC_LEN:
        print("  too short to be a frame")
        return 1
    if crc16(frame) == RESIDUE:
        print("  CRC already passes")
        return 0

    found = repairing_bits(frame)
    if not found:
        print("  no single bit repairs it, more than one error or not bit damage")
        return 1
    for offset, bit in found:
        print(f"  byte {offset} bit {bit}, {frame[offset]:02x} -> {frame[offset] ^ (1 << bit):02x}, "
              f"{where(offset, len(frame))}")
    if len(found) > 1:
        print(f"  {len(found)} candidates, can't tell them apart, no conclusion")
        return 1
    print("  one candidate, likely a single bit error")

    if args.write_reconstruction is not None:
        # the capture directory only ever holds what the pump sent
        out_dir = os.path.dirname(os.path.abspath(args.write_reconstruction))
        if out_dir == os.path.dirname(os.path.abspath(args.bad_file)):
            print("  not written, pick a path outside the capture directory")
            return 1
        offset, bit = found[0]
        with open(args.write_reconstruction, "xb") as file:
            file.write(flip(frame, offset, bit))
        print(f"  reconstruction written to {args.write_reconstruction}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
