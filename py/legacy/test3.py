"""
Test 3. Pull every trace of the most recent dispense profile and save the raw bytes, one file per trace, without
decoding anything. profile_decode.py turns the saved files into a CSV with no pump needed.
"""

import sys
from pathlib import Path

# run from in here, so the shared modules a folder up aren't importable until it's on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from util import deprecated
deprecated()

import json
import os
import time
from dataclasses import asdict

from capture import INFO_FILE_NAME, trace_file_name
from ncs_client import NCSClient, NCSError
from ncs_protocol import ReturnCode
from pump_client import PumpClient, PumpProtocolError, PumpTransportError
from pump_probe import COMMAND_GAP_SEC, TIMEOUT_RECOVERY_SEC, code_name, print_rows
from pump_protocol import PumpCommand
from pump_structs import (
    COMPRESSED_ENCODING,
    ENCODING_MASK,
    MAX_TRACE_WORDS,
    PROFILE_TRACE_NAMES,
    PROFILE_TYPE_SHIFT,
    WORD_LEN,
    ProfileTraceInfo,
    profile_info_request,
)

# 2:10.25.85.52:8888:COM8
HOST, PORT, PUMP_COM, PUMP_ADDR = "10.25.85.52", 8888, 8, 2


# smallest encoded size first, so the simplest traces are known good before the large pressure traces, which lean
# hardest on the delta coding, are pulled
TRACE_ORDER = [7, 9, 8, 5, 6, 2, 3, 4, 1, 0]

MAX_RETRIES = 3
RETRY_GATEWAY_CODES = [ReturnCode.SERIAL_OUTPUT_INVALID_CRC, ReturnCode.SERIAL_READ_TIMEOUT]

def bad_file_name(trace, start):
    """
    Where a reply that failed its checks is kept. Never read back as trace data, it's only for diagnosis.
    """
    return f"trace_{trace}_{start}.bad"


def should_retry(error):
    return isinstance(error, PumpTransportError) and error.gateway_rc in RETRY_GATEWAY_CODES


def report_failure(what, error):
    """
    Print why a command failed, and wait out a pump timeout so the next command isn't lost to it as well.
    """
    print(f"  {what} -> {type(error).__name__}: {error}")
    if isinstance(error, PumpTransportError) and error.gateway_rc == ReturnCode.SERIAL_READ_TIMEOUT:
        time.sleep(TIMEOUT_RECOVERY_SEC)


def read_profile_cycle(pump):
    """
    The most recent profile cycle from CS_RTSTATUS, or from RTSTATUS if that fails. None if neither gives one.
    """
    for name, read in [("CS_RTSTATUS", pump.get_cs_rt_status), ("RTSTATUS", pump.get_rt_status)]:
        print(f"\n * {name}\n")
        time.sleep(COMMAND_GAP_SEC)
        try:
            status = read()
        except Exception as error:
            report_failure(name, error)
            continue
        print(f"  ProfileCycle: {status.profile_cycle}")
        if status.profile_cycle is not None:
            return status.profile_cycle
    return None


def read_profile_info(pump, cycle):
    """
    The profile info for one cycle, decoded, with the raw reply. Both None if the pump didn't answer.
    """
    print(f"\n * GET_PROFILE_TRACE_INFO, cycle {cycle}\n")
    time.sleep(COMMAND_GAP_SEC)
    try:
        raw = pump.read_data(PumpCommand.GET_PROFILE_TRACE_INFO, profile_info_request(cycle))
    except Exception as error:
        report_failure("GET_PROFILE_TRACE_INFO", error)
        return None, None

    info = ProfileTraceInfo.parse(raw)
    if not info.length_matches():
        print(f"  LENGTH MISMATCH: {len(raw)} bytes, expected {info.length()}")
    print_rows(info.rows())
    return info, raw





def request_chunk(pump, cycle, trace, start, count):
    """
    One chunk of a trace, retried when the bus lost or damaged it. Returns the bytes, or None and the last error,
    and how many requests it took.
    """
    attempts = 0
    while True:
        attempts += 1
        time.sleep(COMMAND_GAP_SEC)
        try:
            return pump.get_profile_trace(cycle, trace, start, count), None, attempts
        except Exception as error:
            report_failure(f"words {start} to {start + count - 1}, attempt {attempts}", error)
            if attempts > MAX_RETRIES or not should_retry(error):
                return None, error, attempts


def pull_trace(pump, cycle, trace, expected_words, directory):
    """
    All the words of one trace, a chunk at a time. Returns the bytes received and a record of every chunk.
    """
    data = b""
    chunks = []
    start = 0
    while start < expected_words:
        count = min(MAX_TRACE_WORDS, expected_words - start)
        chunk, error, attempts = request_chunk(pump, cycle, trace, start, count)
        record = {"start": start, "count": count, "attempts": attempts, "failed": False, "bad_file": None}
        chunks.append(record)

        if chunk is None:
            # kept apart from the trace so nothing downstream takes it for checked data
            if isinstance(error, (PumpTransportError, PumpProtocolError)) and error.body:
                record["bad_file"] = bad_file_name(trace, start)
                with open(os.path.join(directory, record["bad_file"]), "wb") as file:
                    file.write(error.body)
                print(f"  {len(error.body)} bytes kept in {record['bad_file']}")
            record["failed"] = True
            break

        received_words = len(chunk) // WORD_LEN
        print(f"  words {start} to {start + count - 1} received {received_words} of {count}")
        data += chunk

        # an empty answer would otherwise ask for the same words forever
        if received_words == 0:
            print("  empty reply, trace stopped")
            break
        start += received_words

    return data, chunks


def write_info(directory, saved_info):
    with open(os.path.join(directory, INFO_FILE_NAME), "w") as file:
        json.dump(saved_info, file, indent=4)


def pull_counts(saved_info):
    """
    Requests sent, retries among them and chunks given up on, across every trace pulled so far.
    """
    round_trips = 0
    retries = 0
    failed = 0
    for chunks in saved_info["chunks"].values():
        for record in chunks:
            round_trips += record["attempts"]
            retries += record["attempts"] - 1
            if record["failed"]:
                failed += 1
    return round_trips, retries, failed


def main():
    print(f"NCS on {HOST}:{PORT}, pump address {PUMP_ADDR} on COM{PUMP_COM}")

    ncs = NCSClient(HOST, PORT)
    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except NCSError as error:
        print(f"  GET_VERSION -> {type(error).__name__}: {error}")
        print("\n  NCS not answering")
        return 1

    pump = PumpClient(ncs, PUMP_COM, PUMP_ADDR)

    # the pump keeps dispensing while this runs, so one cycle number is fixed here and used for every request
    cycle = read_profile_cycle(pump)
    if cycle is None:
        print("\n  FAILED, no profile cycle")
        return 1

    info, info_raw = read_profile_info(pump, cycle)
    if info is None:
        print(f"\n  FAILED, no profile info for cycle {cycle}")
        return 1
    if info.cycle != cycle:
        print(f"\n  FAILED, asked cycle {cycle}, got {info.cycle}")
        return 1
    if info.encoded_size is None or info.unencoded_size is None:
        print("\n  FAILED, profile info too short")
        return 1

    encoding = info.type_encoding & ENCODING_MASK
    profile_type = info.type_encoding >> PROFILE_TYPE_SHIFT
    print(f"\n  encoding {encoding}, type {profile_type}, {info.unencoded_size} samples")
    if encoding != COMPRESSED_ENCODING:
        print(f"  UNKNOWN ENCODING {encoding}")

    directory = os.path.join("captures", f"profile_{cycle}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(directory)
    saved_info = asdict(info)
    saved_info["raw_hex"] = info_raw.hex()
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

    # asked again with the same cycle, so a profile that changed or went away during the pull is caught
    recheck, recheck_raw = read_profile_info(pump, cycle)
    unchanged = recheck_raw == info_raw
    if recheck is None:
        print("\n  profile info re-read failed")
    elif not unchanged:
        print("\n  PROFILE INFO CHANGED DURING PULL")

    round_trips, retries, failed = pull_counts(saved_info)
    print("\n * SUMMARY\n")
    print(f"  cycle {cycle}, {elapsed:.1f}s, {total_bytes} bytes")
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


if __name__ == "__main__":
    sys.exit(main())
