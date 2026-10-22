"""
Where outputs go on disk, so extract and render agree on it.

    <out>/<serial>/pump.json
    <out>/<serial>/<cycle>/dispense.json, trace.csv
    <out>/<serial>/<cycle>/captures/<run time>/     the raw capture, one folder per collection
    <out>/runs/<run time>/run.json, comms/<address>/
"""

import json
import os
import re

PUMP_FILE_NAME = "pump.json"
DISPENSE_FILE_NAME = "dispense.json"
TRACE_FILE_NAME = "trace.csv"
CAPTURES_DIR_NAME = "captures"
RUNS_DIR_NAME = "runs"
RUN_FILE_NAME = "run.json"
COMMS_DIR_NAME = "comms"
RUN_TIME_FORMAT = "%Y%m%d_%H%M%S"

# anything a folder name can't safely hold on every system becomes an underscore
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(text):
    return UNSAFE.sub("_", str(text))


def pump_dir(out, serial):
    return os.path.join(out, safe_name(serial))


def cycle_dir(out, serial, cycle):
    return os.path.join(pump_dir(out, serial), str(cycle))


def capture_dir(out, serial, cycle, run_time):
    return os.path.join(cycle_dir(out, serial, cycle), CAPTURES_DIR_NAME, run_time)


def run_dir(out, run_time):
    return os.path.join(out, RUNS_DIR_NAME, run_time)


def comms_dir(out, run_time, address):
    return os.path.join(run_dir(out, run_time), COMMS_DIR_NAME, safe_name(address))


def read_dispense(out, serial, cycle):
    """
    The dispense row already written for a cycle, or None.
    """
    path = os.path.join(cycle_dir(out, serial, cycle), DISPENSE_FILE_NAME)
    if not os.path.exists(path):
        return None
    with open(path) as file:
        return json.load(file)


def flaws(dispense):
    """
    How far a dispense row is from clean, for comparing two collections of one cycle. Lower is better.
    """
    return len(dispense["blank_traces"]), dispense["reconstructed"]


def already_clean(out, serial, cycle):
    saved = read_dispense(out, serial, cycle)
    return saved is not None and flaws(saved) == (0, False)
