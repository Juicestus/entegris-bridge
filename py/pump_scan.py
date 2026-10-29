"""
Find the pumps behind one NCS box by asking every address on every COM port for its serial number.

    python pump_scan.py 10.25.85.52:8888 [--com 1,2,8] [--addrs 1-63] [--timeout-ms 5000]

Only the serial number read is sent, the same one the bridge starts every pump with. Prints each pump found as
addr:host:port:COMn, ready to paste into pumps.csv.
"""

import argparse
import sys

from ncs_client import TIMEOUT_MS, NCSClient, NCSError, ReturnCodeError
from ncs_protocol import COM_PORT_COUNT, ReturnCode
from pump_client import PumpClient, PumpError, PumpTransportError
from pump_config import MIN_COM, PumpAddress
from pump_protocol import MAX_ADDR, MIN_ADDR, PumpCommand
from pump_reader import paced
from pump_structs import SerialNumbers

# a garbled reply could still be a pump, so it's asked again before being written off
ATTEMPTS = 2
UNREADABLE = "?"


def parse_list(text):
    return [int(part) for part in text.split(",")]


def parse_range(text):
    first, last = text.split("-")
    return list(range(int(first), int(last) + 1))


def probe(pump):
    """
    The pump's serial number, None if nothing answered, or UNREADABLE if something answered but couldn't be read.
    """
    for attempt in range(ATTEMPTS):
        try:
            return paced(pump.read_struct, PumpCommand.SERIALNUMBERS, SerialNumbers).pump or UNREADABLE
        except PumpTransportError as error:
            if error.gateway_rc == ReturnCode.SERIAL_READ_TIMEOUT:
                return None
        except PumpError:
            pass
    return UNREADABLE


def scan(ncs, coms, addrs, found):
    for com in coms:
        try:
            ncs.query_interface(com)
        except ReturnCodeError as error:
            print(f"  COM{com}  skipped, {error}")
            continue

        count = 0
        for addr in addrs:
            print(f"  COM{com}  {addr}   ", end="\r", flush=True)
            serial = probe(PumpClient(ncs, com, addr))
            if serial is None:
                continue
            address = PumpAddress(addr, ncs.host, ncs.port, com)
            print(f"  COM{com}  {addr}  {serial}")
            found.append((address, serial))
            count += 1
        print(f"  COM{com}  {count} found")


def main():
    parser = argparse.ArgumentParser(description="Find the pumps behind one NCS box.")
    parser.add_argument("box", help="NCS box as host:port")
    parser.add_argument("--com", type=parse_list, default=list(range(MIN_COM, COM_PORT_COUNT + 1)),
                        help="COM ports to scan, such as 1,2,8. default all")
    parser.add_argument("--addrs", type=parse_range, default=list(range(MIN_ADDR, MAX_ADDR + 1)),
                        help=f"pump addresses to try, such as 1-10. default {MIN_ADDR}-{MAX_ADDR}")
    parser.add_argument("--timeout-ms", type=int, default=TIMEOUT_MS, help="how long the box waits for each pump")
    args = parser.parse_args()

    host, port = args.box.rsplit(":", 1)
    ncs = NCSClient(host, int(port), timeout_ms=args.timeout_ms)
    print(f"{host}:{port}  COM {','.join(map(str, args.com))}  addresses {args.addrs[0]}-{args.addrs[-1]}")

    found = []
    try:
        scan(ncs, args.com, args.addrs, found)
    except NCSError as error:
        print(f"\n  stopped, {type(error).__name__}: {error}")
    except KeyboardInterrupt:
        print("\n  stopped")

    print()
    for address, serial in found:
        print(f"{address}  {serial}")
    if not found:
        print("  no pumps found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
