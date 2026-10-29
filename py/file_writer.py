"""
Outputs as local files. Tables and objects are laid out the way a database and a bucket would hold them, and the
raw captures and run logs sit beside them.

    <out>/tables/<table>/<key>.json             one row per file, named by its key columns
    <out>/objects/<path>.csv                    one profile per file
    <out>/collected/<serial>.json               how clean each recent cycle came out, whatever the writer
    <out>/captures/<serial>/<cycle>/<run time>/ the raw capture, one folder per collection
    <out>/runs/<run time>/run.json, comms/<address>/
"""

import json
import os
import re

import pandas as pd

from writer_base import DBWriter

TABLES_DIR_NAME = "tables"
OBJECTS_DIR_NAME = "objects"
CAPTURES_DIR_NAME = "captures"
RUNS_DIR_NAME = "runs"
RUN_FILE_NAME = "run.json"
COMMS_DIR_NAME = "comms"
RUN_TIME_FORMAT = "%Y%m%d_%H%M%S"
COLLECTED_DIR_NAME = "collected"
# comfortably more than the pump holds, so any cycle that can still be pulled again is remembered
KEPT_CYCLES = 10
TEMP_EXTENSION = ".tmp"
ROW_EXTENSION = ".json"
OBJECT_EXTENSION = ".csv"
KEY_SEPARATOR = "_"

# anything a folder name can't safely hold on every system becomes an underscore
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def write_json(path, value):
    with open(path, "w") as file:
        json.dump(value, file, indent=4)
        file.write("\n")


def safe_name(text):
    return UNSAFE.sub("_", str(text))


def capture_dir(out, serial, cycle, run_time):
    return os.path.join(out, CAPTURES_DIR_NAME, safe_name(serial), str(cycle), run_time)


def run_dir(out, run_time):
    return os.path.join(out, RUNS_DIR_NAME, run_time)


def comms_dir(out, run_time, address):
    return os.path.join(run_dir(out, run_time), COMMS_DIR_NAME, safe_name(address))


def row_path(out, table, row, keys):
    name = KEY_SEPARATOR.join(safe_name(row[column]) for column in keys)
    return os.path.join(out, TABLES_DIR_NAME, table, name + ROW_EXTENSION)


def object_path(out, path):
    return os.path.join(out, OBJECTS_DIR_NAME, *[safe_name(part) for part in path.split("/")]) + OBJECT_EXTENSION


def collected_path(out, serial):
    return os.path.join(out, COLLECTED_DIR_NAME, safe_name(serial) + ROW_EXTENSION)


class CollectedCycles:
    """
    How clean each recent cycle of one pump came out, kept on this machine so the database is only ever written to.
    Only cycles the pump might still hold are worth remembering, older ones are dropped.
    """
    def __init__(self, out, serial):
        self.path = collected_path(out, serial)
        self.cycles = {}
        if os.path.exists(self.path):
            with open(self.path) as file:
                self.cycles = json.load(file)

    def get(self, cycle):
        """
        The saved blank_traces and reconstructed for a cycle, or None if it was never collected.
        """
        return self.cycles.get(str(cycle))

    def set(self, cycle, blank_traces, reconstructed):
        self.cycles[str(cycle)] = {"blank_traces": blank_traces, "reconstructed": reconstructed}
        newest = max(int(key) for key in self.cycles)
        self.cycles = {key: value for key, value in self.cycles.items() if int(key) > newest - KEPT_CYCLES}

        # written aside then swapped in, so a crash mid write can't leave a file that breaks every later run
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_json(self.path + TEMP_EXTENSION, self.cycles)
        os.replace(self.path + TEMP_EXTENSION, self.path)


class FileDBWriter(DBWriter):
    def __init__(self, out):
        self.out = out

    def write_row(self, table: str, row: dict, keys: list):
        path = row_path(self.out, table, row, keys)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json(path, row)

    def write_obj(self, path: str, data: pd.DataFrame):
        full_path = object_path(self.out, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        data.to_csv(full_path, index=False, lineterminator="\n")
        # relative to the output folder, so the folder can be moved or zipped without breaking the link
        return os.path.relpath(full_path, self.out).replace(os.sep, "/")
