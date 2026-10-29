"""
Collect from one pump and render what was collected. bridge.py decides which pumps and when.
"""

import time
from dataclasses import dataclass, field

import file_writer
from capture import Capture
from comms_log import CommsLog
from file_writer import CollectedCycles
from ncs_client import NCSClient, NCSError
from profile_pull import pull_profile
from pump_client import PumpClient, PumpError
from pump_reader import read_dispense_cycle, read_dispense_header, read_newest_cycle, read_pump_info, read_profile_info
from render import WriteError, already_clean, render, write_pump

# the pump keeps this many profiles, the newest and the ones before it
HELD_CYCLES = 3

SAVE_OUTPUTS = 0        # only the rows and profiles
SAVE_CAPTURE = 1        # also the raw capture, enough to render again or look into a failure
SAVE_COMMS = 2          # also every request and reply sent to the NCS


@dataclass
class Options:
    out: str                # raw captures, comms logs and run.json always go here
    save: int
    run_time: str
    writers: list           # every destination gets its own copy of every row and profile


@dataclass
class CycleResult:
    cycle: int
    state: str              # what happened, one short line
    cycles_behind: int = 0
    reconstructed: bool = False
    blank_traces: list = field(default_factory=list)
    retries: int = 0
    written: bool = False


@dataclass
class PumpResult:
    label: str
    address: str
    serial: str = None
    cycles: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    error: str = None       # why the pump couldn't be finished


def collect_cycle(pump, pump_info, address, cycle, newest, header):
    """
    The capture of one cycle, or None if the pump no longer holds it.
    """
    collected_at = time.time()
    info, info_raw = read_profile_info(pump, cycle)
    if info is None:
        return None
    if info.cycle != cycle:
        raise PumpError(f"asked for cycle {cycle}, the profile info is for {info.cycle}")

    traces = pull_profile(pump, cycle, info)
    capture = Capture(str(address), cycle, newest, collected_at, pump_info, info, info_raw, traces)

    # DISPENSE_INFO only ever describes the newest dispense, and is only trusted if no dispense happened during the
    # pull, so it's asked again afterwards
    if header.cycle is None:
        capture.header_problem = f"DISPENSE_INFO not read: {header.problems}"
        return capture
    if header.cycle != cycle:
        capture.header_problem = f"DISPENSE_INFO describes cycle {header.cycle} only"
        return capture
    try:
        after = read_dispense_cycle(pump)
    except PumpError as error:
        capture.header_problem = f"DISPENSE_INFO re-read failed: {error}"
        return capture
    if after != cycle:
        capture.header_problem = f"pump dispensed cycle {after} during the pull"
        return capture
    capture.header = header
    return capture


def cycle_line(result):
    line = f"  {result.cycle} {result.state}"
    if result.cycles_behind:
        line += f", {result.cycles_behind} behind"
    if result.retries:
        line += f", {result.retries} retries"
    return line


def extract_pump(config, options):
    """
    Everything for one pump. Talks to nothing but this pump's NCS and writes nothing but this pump's outputs, so
    pumps on different buses can later run side by side.
    """
    address = config.address
    result = PumpResult(config.label(), str(address))
    log = CommsLog() if options.save >= SAVE_COMMS else None
    pump = PumpClient(NCSClient(address.host, address.port, log=log), address.com, address.addr)
    print(result.label)

    try:
        pump_info = read_pump_info(pump)
        result.serial = pump_info.serial
        result.problems += pump_info.problems
        write_pump(pump_info, address, options.writers)
        print(f"  {pump_info.serial}")
        for problem in pump_info.problems:
            print(f"  ! {problem}")

        newest = read_newest_cycle(pump)
        if newest is None:
            raise PumpError("no profile cycle from CS_RTSTATUS or RTSTATUS")
        header = read_dispense_header(pump)
        collected = CollectedCycles(options.out, pump_info.serial)

        for cycle in range(newest, max(newest - HELD_CYCLES, -1), -1):
            if already_clean(collected, cycle):
                cycle_result = CycleResult(cycle, "already collected", newest - cycle)
                result.cycles.append(cycle_result)
                print(cycle_line(cycle_result))
                continue
            capture = collect_cycle(pump, pump_info, address, cycle, newest, header)
            if capture is None:
                cycle_result = CycleResult(cycle, "not held", newest - cycle)
                result.cycles.append(cycle_result)
                print(cycle_line(cycle_result))
                continue

            if options.save >= SAVE_CAPTURE:
                capture.write_raw(file_writer.capture_dir(options.out, pump_info.serial, cycle, options.run_time))
            record, written = render(capture, options.writers, collected)

            state = "OK"
            if record.blank_traces:
                state = f"BLANK traces {record.blank_traces}"
            elif record.reconstructed:
                state = f"RECONSTRUCTED, trace {sorted({repair['trace'] for repair in record.repairs})}"
            if not written:
                state += ", kept the cleaner copy already saved"
            if not record.header_available:
                state += ", no header"
            cycle_result = CycleResult(cycle, state, record.cycles_behind, record.reconstructed,
                                       record.blank_traces, record.retries, written)
            result.cycles.append(cycle_result)
            print(cycle_line(cycle_result))

    except (NCSError, PumpError, WriteError) as error:
        result.error = f"{type(error).__name__}: {error}"
    except Exception as error:
        # anything at all, one pump going wrong must never stop the pumps after it
        result.error = f"unexpected {type(error).__name__}: {error}"
    finally:
        if log is not None:
            log.write(file_writer.comms_dir(options.out, options.run_time, address))

    if result.error is not None:
        print(f"  FAILED, {result.error}")
    return result

