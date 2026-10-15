"""
Test 4. Look for profiles other than the live one, such as a stored reference, by asking for the profile info of a
handful of cycle numbers. Nothing is pulled unless one cycle is given with --pull.
"""

import json
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass

from capture_format import INFO_FILE_NAME, WORD_LEN, trace_file_name
from ncs_client import NCSClient, NCSError
from pump_client import PumpClient
from pump_probe import COMMAND_GAP_SEC, code_name
from pump_protocol import PumpCommand, PumpReturnCode
from pump_structs import (
    COMPRESSED_ENCODING,
    ENCODING_MASK,
    PROFILE_TRACE_NAMES,
    PROFILE_TYPE_SHIFT,
    ProfileTraceInfo,
    profile_info_request,
)
from test3 import CAPTURE_ROOT, TRACE_ORDER, pull_trace, read_profile_cycle, read_profile_info, report_failure

# 2:10.25.85.52:8888:COM8
HOST = "10.25.85.52"
PORT = 8888
PUMP_COM = 8
PUMP_ADDR = 2

PULL_FLAG = "--pull"

# counted back from the live cycle, to find how far back the pump keeps profiles
LIVE_OFFSETS = [1, 2, 5, 10, 50]

# zero means the most recent dispense for DISPENSE_INFO, one is the lowest real cycle, and all ones is a common way
# of naming a special record. only the cycle number is ever varied, never the command
FIXED_CANDIDATES = [0, 1, 0xFFFFFFFF]

# a bounds error carries the error code again and the index of the request field that was out of bounds
BOUNDS_ERROR_FORMAT = "<HH"


@dataclass
class Answer:
    """
    What one cycle number got back.
    """
    asked: int
    rc: object = None           # the pump's return code, when a reply came back
    detail: str = ""            # the bounds error's field, when there is one
    failure: str = None         # why no reply came back
    info: ProfileTraceInfo = None

    def is_alias(self):
        """
        Whether the pump answered with a profile for a different cycle than the one asked for.
        """
        return self.info is not None and self.info.cycle is not None and self.info.cycle != self.asked


def profile_type(info):
    if info.type_encoding is None:
        return None
    return info.type_encoding >> PROFILE_TYPE_SHIFT


def encoding_of(info):
    if info.type_encoding is None:
        return None
    return info.type_encoding & ENCODING_MASK


def connect():
    """
    A pump client, or None if the NCS isn't answering for itself.
    """
    print(f"NCS on {HOST}:{PORT}, pump address {PUMP_ADDR} on COM{PUMP_COM}")
    ncs = NCSClient(HOST, PORT)
    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except NCSError as error:
        print(f"  GET_VERSION -> {type(error).__name__}: {error}")
        print("\n  NCS not answering")
        return None
    return PumpClient(ncs, PUMP_COM, PUMP_ADDR)


def candidate_cycles(live):
    """
    The cycle numbers to ask about, the live one first so a known good answer comes before anything speculative.
    """
    cycles = [live]
    for offset in LIVE_OFFSETS:
        if live - offset < 0:
            print(f"  skipped {live} - {offset}, below zero")
            continue
        cycles.append(live - offset)
    for cycle in FIXED_CANDIDATES:
        if cycle not in cycles:
            cycles.append(cycle)
    return cycles


def ask(pump, cycle):
    """
    Send one GET_PROFILE_TRACE_INFO for a cycle and keep whatever came back.
    """
    answer = Answer(cycle)
    print(f"\n * GET_PROFILE_TRACE_INFO, cycle {cycle}\n")
    time.sleep(COMMAND_GAP_SEC)
    try:
        reply = pump.command(PumpCommand.GET_PROFILE_TRACE_INFO, profile_info_request(cycle))
    except Exception as error:
        report_failure(f"cycle {cycle}", error)
        answer.failure = f"{type(error).__name__}: {error}"
        return answer

    answer.rc = reply.rc
    if reply.rc == PumpReturnCode.GOOD:
        answer.info = ProfileTraceInfo.parse(reply.data)
    elif reply.rc == PumpReturnCode.BOUNDS_ERROR and len(reply.data) >= struct.calcsize(BOUNDS_ERROR_FORMAT):
        field_index = struct.unpack_from(BOUNDS_ERROR_FORMAT, reply.data)[1]
        answer.detail = f", field {field_index}"
    return answer


def print_table(answers, live_type):
    """
    One row per cycle asked about, with what made it interesting said at the end of the row.
    """
    print(f"  {'asked':>10}  {'answer':<24}  {'reported':>10}  {'type':>4}  {'enc':>3}  {'samples':>7}  {'words':>6}")
    for answer in answers:
        if answer.failure is not None:
            print(f"  {answer.asked:>10}  {answer.failure}")
            continue

        result = code_name(answer.rc) + answer.detail
        if answer.info is None:
            print(f"  {answer.asked:>10}  {result:<24}")
            continue

        info = answer.info
        words = None
        if info.encoded_size is not None:
            words = sum(info.encoded_size)
        print(f"  {answer.asked:>10}  {result:<24}  {str(info.cycle):>10}  {str(profile_type(info)):>4}  "
              f"{str(encoding_of(info)):>3}  {str(info.unencoded_size):>7}  {str(words):>6}", end="")

        notes = []
        if answer.is_alias():
            notes.append(f"ALIAS of {info.cycle}")
        if live_type is not None and profile_type(info) != live_type:
            notes.append("NEW TYPE")
        print(f"  <- {', '.join(notes)}" if notes else "")


def probe(pump):
    """
    Mode 1, ask about every candidate cycle and suggest which one to pull. Returns the exit code.
    """
    live = read_profile_cycle(pump)
    if live is None:
        print("\n  FAILED, no profile cycle")
        return 1

    answers = []
    for cycle in candidate_cycles(live):
        answers.append(ask(pump, cycle))

    live_answer = answers[0]
    live_type = None
    if live_answer.info is not None:
        live_type = profile_type(live_answer.info)

    print(f"\n * CANDIDATES, live cycle {live}\n")
    print_table(answers, live_type)

    answered = [answer for answer in answers if answer.info is not None]
    older = [answer for answer in answered if answer.asked < live and not answer.is_alias()]
    different = []
    if live_type is not None:
        different = [answer for answer in answered if profile_type(answer.info) != live_type]

    print("\n * SUMMARY\n")
    print(f"  GOOD: {', '.join(str(answer.asked) for answer in answered) or 'none'}")
    if live_answer.info is None:
        print("  LIVE CYCLE NOT GOOD")
    if older:
        print(f"  deepest held: {live} - {max(live - answer.asked for answer in older)}")
    else:
        print("  deepest held: none")
    if live_type is not None:
        print(f"  new type: {', '.join(str(answer.asked) for answer in different) or 'none'}")

    # a different profile type is the reference we're after, an older cycle is only another live profile
    print("\n * NEXT\n")
    if different:
        print(f"  python test4.py {PULL_FLAG} {different[0].asked}")
    elif older:
        print(f"  python test4.py {PULL_FLAG} {min(answer.asked for answer in older)}")
    else:
        print("  none")
    return 0


def pull(pump, cycle):
    """
    Mode 2, pull every trace of one cycle into a directory test 4 can decode. Returns the exit code.
    """
    info, info_raw = read_profile_info(pump, cycle)
    if info is None:
        print(f"\n  FAILED, no profile info for cycle {cycle}")
        return 1
    if info.encoded_size is None or info.unencoded_size is None:
        print("\n  FAILED, profile info too short")
        return 1
    if info.cycle != cycle:
        # the value that answered is the one we know works, so every request keeps using it
        print(f"\n  ALIAS of {info.cycle}")

    encoding = encoding_of(info)
    print(f"\n  encoding {encoding}, type {profile_type(info)}, {info.unencoded_size} samples")
    if encoding != COMPRESSED_ENCODING:
        print(f"  UNKNOWN ENCODING {encoding}")

    # marked as test 5 so it can't be mistaken for a capture of a live cycle
    directory = os.path.join(CAPTURE_ROOT, f"test4_profile_{cycle}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(directory)
    saved_info = asdict(info)
    saved_info["raw_hex"] = info_raw.hex()
    saved_info["asked_cycle"] = cycle
    saved_info["captured"] = f"{HOST}:{PORT} COM{PUMP_COM} pump {PUMP_ADDR} at {time.strftime('%Y-%m-%d %H:%M:%S')}"
    with open(os.path.join(directory, INFO_FILE_NAME), "w") as file:
        json.dump(saved_info, file, indent=4)

    start_time = time.monotonic()
    total_bytes = 0
    total_round_trips = 0
    disagreements = []
    for trace in TRACE_ORDER:
        expected_words = info.encoded_size[trace]
        print(f"\n * GET_PROFILE_TRACE, trace {trace} {PROFILE_TRACE_NAMES[trace]}, {expected_words} words\n")
        data, round_trips = pull_trace(pump, cycle, trace, expected_words)

        # written before anything else happens, so whatever goes wrong later these bytes are already on disk
        path = os.path.join(directory, trace_file_name(trace))
        with open(path, "wb") as file:
            file.write(data)

        total_bytes += len(data)
        total_round_trips += round_trips
        received_words = len(data) // WORD_LEN
        if received_words == expected_words:
            print(f"  OK, {received_words} words")
        else:
            print(f"  MISMATCH, {received_words} words, expected {expected_words}")
            disagreements.append(trace)
    elapsed = time.monotonic() - start_time

    # a stored profile must come back exactly the same. if it doesn't, this value is tracking something live
    recheck, recheck_raw = read_profile_info(pump, cycle)
    unchanged = recheck_raw == info_raw
    if recheck is None:
        print("\n  profile info re-read failed")
    elif not unchanged:
        print("\n  PROFILE INFO CHANGED DURING PULL")

    print("\n * SUMMARY\n")
    print(f"  cycle {cycle}, reports {info.cycle}, type {profile_type(info)}")
    print(f"  {elapsed:.1f}s, {total_bytes} bytes, {total_round_trips} round trips")
    print(f"  {directory}")

    print("\n * VERDICT\n")
    if disagreements or not unchanged or encoding != COMPRESSED_ENCODING:
        print("  NOT CLEAN")
        if disagreements:
            print(f"  wrong word count: {disagreements}")
        return 1
    print("  PASSED")
    return 0


def main():
    if len(sys.argv) == 1:
        pull_cycle = None
    elif len(sys.argv) == 3 and sys.argv[1] == PULL_FLAG:
        pull_cycle = int(sys.argv[2], 0)
    else:
        print(f"usage: python test4.py [{PULL_FLAG} <cycle>]")
        return 2

    pump = connect()
    if pump is None:
        return 1
    if pull_cycle is None:
        return probe(pump)
    return pull(pump, pull_cycle)


if __name__ == "__main__":
    sys.exit(main())
