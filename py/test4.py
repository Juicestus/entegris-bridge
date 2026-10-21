"""
Test 4. Look for profiles other than the live one, such as a stored reference, by asking for the profile info of a
handful of cycle numbers. Nothing is pulled unless one cycle is given with --pull.
"""

import os
import struct
import sys
import time
from dataclasses import asdict, dataclass

from capture_format import WORD_LEN, trace_file_name
from ncs_client import NCSClient, NCSError
from pump_client import PumpClient
from pump_probe import COMMAND_GAP_SEC, code_name
from pump_protocol import PumpCommand, PumpReturnCode
from pump_structs import (
    COMPRESSED_ENCODING,
    ENCODING_MASK,
    MOST_RECENT_DISPENSE,
    PROFILE_TRACE_NAMES,
    PROFILE_TYPE_SHIFT,
    ProfileTraceInfo,
    dispense_info_known_rows,
    dispense_info_request,
    profile_info_request,
)
from test3 import (
    CAPTURE_ROOT,
    TRACE_ORDER,
    pull_counts,
    pull_trace,
    read_profile_cycle,
    read_profile_info,
    report_failure,
    write_info,
)

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

# a DISPENSE_INFO field that may name the cycle of a stored reference profile, our only lead on addressing one
REFERENCE_CYCLE_LABEL = "ReferenceCycle"

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


def read_reference_cycle(pump):
    """
    The ReferenceCycle DISPENSE_INFO reports for the most recent dispense, or None if it didn't give one.
    """
    print(f"\n * DISPENSE_INFO, recipe {MOST_RECENT_DISPENSE}\n")
    time.sleep(COMMAND_GAP_SEC)
    try:
        data = pump.read_data(PumpCommand.DISPENSE_INFO, dispense_info_request(MOST_RECENT_DISPENSE))
    except Exception as error:
        report_failure("DISPENSE_INFO", error)
        return None

    for label, value, unit in dispense_info_known_rows(data):
        if label == REFERENCE_CYCLE_LABEL:
            print(f"  {label}: {value}")
            return value
    return None


def candidate_cycles(live, reference):
    """
    The cycle numbers to ask about, the live one first so a known good answer comes before anything speculative.
    """
    cycles = [live]
    for offset in LIVE_OFFSETS:
        if live - offset < 0:
            print(f"  skipped {live} - {offset}, below zero")
            continue
        cycles.append(live - offset)
    if reference is not None and reference not in cycles:
        cycles.append(reference)
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


def print_table(answers, live_type, reference):
    """
    One row per cycle asked about, with what made it interesting said at the end of the row.
    """
    print(f"  {'asked':>10}  {'answer':<24}  {'reported':>10}  {'type':>4}  {'enc':>3}  {'samples':>7}  {'words':>6}")
    for answer in answers:
        source = ""
        if answer.asked == reference:
            source = f"  <- {REFERENCE_CYCLE_LABEL}"
        if answer.failure is not None:
            print(f"  {answer.asked:>10}  {answer.failure}{source}")
            continue

        result = code_name(answer.rc) + answer.detail
        if answer.info is None:
            print(f"  {answer.asked:>10}  {result:<24}{source}")
            continue

        info = answer.info
        words = None
        if info.encoded_size is not None:
            words = sum(info.encoded_size)
        print(f"  {answer.asked:>10}  {result:<24}  {str(info.cycle):>10}  {str(profile_type(info)):>4}  "
              f"{str(encoding_of(info)):>3}  {str(info.unencoded_size):>7}  {str(words):>6}", end="")

        notes = []
        if answer.asked == reference:
            notes.append(REFERENCE_CYCLE_LABEL)
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

    reference = read_reference_cycle(pump)

    answers = []
    for cycle in candidate_cycles(live, reference):
        answers.append(ask(pump, cycle))

    live_answer = answers[0]
    live_type = None
    if live_answer.info is not None:
        live_type = profile_type(live_answer.info)

    print(f"\n * CANDIDATES, live cycle {live}\n")
    print_table(answers, live_type, reference)

    answered = [answer for answer in answers if answer.info is not None]
    # the reference isn't one of the live profiles, so counting it would throw off how deep those go
    older = [answer for answer in answered
             if answer.asked < live and not answer.is_alias() and answer.asked != reference]
    reference_held = [answer for answer in answered if answer.asked == reference]
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
    elif reference_held:
        print(f"  python test4.py {PULL_FLAG} {reference}")
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

    # marked as test 4 so it can't be mistaken for a capture of a live cycle
    directory = os.path.join(CAPTURE_ROOT, f"test4_profile_{cycle}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(directory)
    saved_info = asdict(info)
    saved_info["raw_hex"] = info_raw.hex()
    saved_info["asked_cycle"] = cycle
    saved_info["captured"] = f"{HOST}:{PORT} COM{PUMP_COM} pump {PUMP_ADDR} at {time.strftime('%Y-%m-%d %H:%M:%S')}"
    saved_info["chunks"] = {}
    write_info(directory, saved_info)

    start_time = time.monotonic()
    total_bytes = 0
    disagreements = []
    for trace in TRACE_ORDER:
        expected_words = info.encoded_size[trace]
        print(f"\n * GET_PROFILE_TRACE, trace {trace} {PROFILE_TRACE_NAMES[trace]}, {expected_words} words\n")
        data, chunks = pull_trace(pump, cycle, trace, expected_words, directory)

        # written before anything else happens, so whatever goes wrong later these bytes are already on disk
        path = os.path.join(directory, trace_file_name(trace))
        with open(path, "wb") as file:
            file.write(data)
        saved_info["chunks"][trace] = chunks
        write_info(directory, saved_info)

        total_bytes += len(data)
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

    round_trips, retries, failed = pull_counts(saved_info)
    print("\n * SUMMARY\n")
    print(f"  cycle {cycle}, reports {info.cycle}, type {profile_type(info)}")
    print(f"  {elapsed:.1f}s, {total_bytes} bytes")
    print(f"  {round_trips} round trips, {retries} retries, {failed} failed")
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
