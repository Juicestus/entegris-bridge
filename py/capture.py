"""
Everything collected for one dispense cycle, exactly as the pump gave it. Extract builds one, render turns it into
outputs, and in between it can be written to disk and read back so render can run again later with no pump.
"""

import json
import os
from dataclasses import asdict, dataclass

from profile_pull import ChunkRecord, Repair, TracePull
from pump_reader import DispenseHeader, PumpInfo
from pump_structs import ProfileTraceInfo

# the profile info and trace file names are also what the old test 3 wrote, so its captures decode the same way
INFO_FILE_NAME = "profile_info.json"
CAPTURE_FILE_NAME = "capture.json"


def trace_file_name(trace):
    return f"trace_{trace}.bin"


def bad_file_name(trace, start, attempt):
    """
    Where a reply that failed its checks is kept. Never read back as trace data, it's only for diagnosis.
    """
    return f"trace_{trace}_{start}_{attempt}.bad"


@dataclass
class Capture:
    address: str                # the config's address string
    cycle: int
    newest_cycle: int           # the pump's newest cycle when this one was collected
    collected_at: float         # host clock, unix seconds
    pump: PumpInfo
    info: ProfileTraceInfo
    info_raw: bytes
    traces: list                # TracePull per trace, in trace number order
    header: DispenseHeader = None   # only when it's certainly for this cycle
    header_problem: str = None      # why there's no header

    def reconstructed(self):
        return any(pull.repaired() for pull in self.traces)

    def write_raw(self, directory):
        """
        The profile info and every trace as received, every reply that failed its checks, and the rest as JSON.
        """
        os.makedirs(directory, exist_ok=True)
        info = asdict(self.info)
        info["raw_hex"] = self.info_raw.hex()
        write_json(os.path.join(directory, INFO_FILE_NAME), info)

        traces = []
        for pull in self.traces:
            with open(os.path.join(directory, trace_file_name(pull.trace)), "wb") as file:
                file.write(pull.data)
            chunks = []
            for chunk in pull.chunks:
                bad_files = []
                for attempt, frame in chunk.bad_replies:
                    name = bad_file_name(pull.trace, chunk.start, attempt)
                    with open(os.path.join(directory, name), "wb") as file:
                        file.write(frame)
                    bad_files.append([attempt, name])
                record = asdict(chunk)
                record["bad_replies"] = bad_files
                chunks.append(record)
            traces.append({"trace": pull.trace, "expected_words": pull.expected_words, "chunks": chunks})

        write_json(os.path.join(directory, CAPTURE_FILE_NAME), {
            "address": self.address,
            "cycle": self.cycle,
            "newest_cycle": self.newest_cycle,
            "collected_at": self.collected_at,
            "pump": asdict(self.pump),
            "header": None if self.header is None else asdict(self.header),
            "header_problem": self.header_problem,
            "traces": traces,
        })

    @classmethod
    def read_raw(cls, directory):
        with open(os.path.join(directory, CAPTURE_FILE_NAME)) as file:
            saved = json.load(file)
        with open(os.path.join(directory, INFO_FILE_NAME)) as file:
            info_raw = bytes.fromhex(json.load(file)["raw_hex"])

        traces = []
        for saved_trace in saved["traces"]:
            with open(os.path.join(directory, trace_file_name(saved_trace["trace"])), "rb") as file:
                pull = TracePull(saved_trace["trace"], saved_trace["expected_words"], file.read())
            for saved_chunk in saved_trace["chunks"]:
                bad_replies = []
                for attempt, name in saved_chunk.pop("bad_replies"):
                    with open(os.path.join(directory, name), "rb") as file:
                        bad_replies.append((attempt, file.read()))
                repair = saved_chunk.pop("repair")
                chunk = ChunkRecord(**saved_chunk, bad_replies=bad_replies)
                if repair is not None:
                    chunk.repair = Repair(**repair)
                pull.chunks.append(chunk)
            traces.append(pull)

        header = None
        if saved["header"] is not None:
            header = DispenseHeader(**saved["header"])
        return cls(
            address=saved["address"],
            cycle=saved["cycle"],
            newest_cycle=saved["newest_cycle"],
            collected_at=saved["collected_at"],
            pump=PumpInfo(**saved["pump"]),
            info=ProfileTraceInfo.parse(info_raw),
            info_raw=info_raw,
            traces=traces,
            header=header,
            header_problem=saved["header_problem"],
        )


def write_json(path, value):
    with open(path, "w") as file:
        json.dump(value, file, indent=4)
        file.write("\n")
