"""
Turn a capture into the outputs: the profile, the dispense row and the pump row. Reads only what it's given, never
a pump, so a capture saved to disk can be rendered again later, after a schema or decoder change.

    python render.py <capture directory>
"""

import os
import struct
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from capture import Capture
from file_writer import CollectedCycles, FileDBWriter
from profile_decode import decode, profile_frame
from pump_structs import COMPRESSED_ENCODING, ENCODING_MASK, PROFILE_TYPE_SHIFT, WORD_LEN
from writer_base import DISPENSE_TABLE, PUMP_TABLE, DBWriter

# increment whenever there is a change that impacts the trace decode/csv
DECODER_VERSION = 3

CPSI_PER_PSI = 100
ML_X1000_PER_ML = 1000
PROFILES_PATH = "profiles"



# rows shaped like the database's pump and dispense tables, which another team owns. each lists the table's columns
# in table order, then extras the table doesn't have yet. anything we can't fill keeps its default, so the JSON
# shows the gap rather than leaving the column out
@dataclass
class PumpRecord:
    pump_id: int = None                 # assigned by the database
    serial_number: str = None
    pump_name: str = None
    model_name: str = None
    firmware_version: str = None
    customer_name: str = None
    fab_name: str = None
    tool_name: str = None
    coater_name: str = None
    nozzle_name: str = None
    resist_name: str = None
    fluid_name: str = None
    system_address: int = None
    connection_details: str = None
    module_type: str = None             # the pump gives a number, the column is text
    created_at: str = None              # assigned by the database

    # extras
    serial_number_from_status: str = None
    read_at: str = None
    pump_clock_offset_sec: float = None     # pump clock minus host clock when read, nothing syncs pump clocks
    problems: list = field(default_factory=list)

    def write(self, writer: DBWriter):
        writer.write_row(PUMP_TABLE, asdict(self))


@dataclass
class DispenseRecord:
    dispense_id: int = None             # assigned by the database
    pump_id: int = None                 # assigned by the database, serial_number below says which pump
    cycle_number: int = None
    triggered_at: str = None            # the pump's own clock
    recipe_number: int = None
    job_info: str = None                # the recipe's name, not a lot or wafer job
    reference_cycle_number: int = None
    firmware_version: str = None
    sample_interval_ms: int = None
    recorded_at: str = None             # when we collected it, host clock

    # extras
    serial_number: str = None
    trace_object_uri: str = None        # where the writer put the profile
    sample_count: int = None            # differs from cycle to cycle
    start_of_cycle_sample: int = None   # the sample at the trigger, time zero in the CSV
    dispense_end_sample: int = None
    filtration_end_sample: int = None
    fill_end_sample: int = None
    end_of_cycle_sample: int = None
    profile_type: int = None
    encoding: int = None
    recipe_title: str = None
    slot_id: int = None
    dispense_volume_setpoint_ul: int = None     # RTSTATUS, appears to be the volume asked for
    dispense_volume_measured_ml: float = None   # LAST_DIS_INFO_2, appears to be the volume measured
    confirmation_pct: int = None
    ready_pressure_psi: float = None
    reference_ready_pressure_psi: float = None
    header_available: bool = False      # whether the fields from DISPENSE_INFO and friends could be filled
    newest_cycle_at_collection: int = None
    cycles_behind: int = None           # more than zero means it was collected after a run missed it
    reconstructed: bool = False         # a trace holds a repaired reply rather than one received intact
    repairs: list = field(default_factory=list)
    blank_traces: list = field(default_factory=list)    # traces left empty in the CSV
    retries: int = 0                    # chunk requests sent again after the bus damaged or lost a reply
    failed_chunks: int = 0
    decoder_version: int = None
    problems: list = field(default_factory=list)

    def write(self, writer: DBWriter):
        writer.write_row(DISPENSE_TABLE, asdict(self))


def iso_time(unix_seconds):
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_traces(capture):
    """
    The samples of every trace, None for a trace that can't be trusted, and a note for each one left out.
    """
    info = capture.info
    notes = []
    if info.type_encoding & ENCODING_MASK != COMPRESSED_ENCODING:
        notes.append(f"unknown encoding {info.type_encoding & ENCODING_MASK}, nothing decoded")
        return [None] * len(capture.traces), notes

    decoded = []
    for pull in capture.traces:
        if not pull.complete():
            notes.append(f"trace {pull.trace}: {len(pull.data) // WORD_LEN} of {pull.expected_words} words")
            decoded.append(None)
            continue
        words = list(struct.unpack(f"<{len(pull.data) // WORD_LEN}H", pull.data))
        trace_notes = []
        try:
            samples = decode(words, info.unencoded_size, trace_notes)
        except ValueError as error:
            samples = None
            trace_notes.append(str(error))
        notes += [f"trace {pull.trace}: {note}" for note in trace_notes]

        # a repair that slipped past the CRC almost never decodes to exactly the right length, so this is the
        # second check on every repaired trace
        if samples is not None and len(samples) != info.unencoded_size:
            notes.append(f"trace {pull.trace}: {len(samples)} samples, expected {info.unencoded_size}")
            samples = None
        if samples is None and pull.repaired():
            notes.append(f"trace {pull.trace}: repaired reply didn't decode, refused")
        decoded.append(samples)
    return decoded, notes


def pump_record(pump, address):
    """
    The pump row from what the pump said about itself and where the config says it is.
    """
    names = pump.names
    return PumpRecord(
        serial_number=pump.serial,
        pump_name=names.get("pump_name"),
        model_name=pump.model,
        firmware_version=pump.firmware,
        customer_name=names.get("customer_name"),
        fab_name=names.get("fab_name"),
        tool_name=names.get("tool_name"),
        coater_name=names.get("coater_name"),
        nozzle_name=names.get("nozzle_name"),
        resist_name=names.get("resist_name"),
        fluid_name=names.get("fluid_name"),
        system_address=address.addr,
        connection_details=str(address),
        module_type=None if pump.module_type is None else str(pump.module_type),
        serial_number_from_status=pump.serial_from_status,
        read_at=iso_time(pump.read_at),
        pump_clock_offset_sec=pump.clock_offset_sec(),
        problems=list(pump.problems),
    )


def dispense_record(capture, decoded, notes):
    """
    The dispense row for a capture. Fields only DISPENSE_INFO and its neighbors can give stay empty when the
    capture has no header for its cycle.
    """
    info = capture.info
    record = DispenseRecord(
        cycle_number=capture.cycle,
        triggered_at=iso_time(info.time),
        firmware_version=capture.pump.firmware,
        sample_interval_ms=info.sample_interval,
        recorded_at=iso_time(capture.collected_at),
        serial_number=capture.pump.serial,
        sample_count=info.unencoded_size,
        start_of_cycle_sample=info.start_cycle,
        dispense_end_sample=info.dispense_end,
        filtration_end_sample=info.end_filtration,
        fill_end_sample=info.fill_end,
        end_of_cycle_sample=info.end_cycle,
        profile_type=info.type_encoding >> PROFILE_TYPE_SHIFT,
        encoding=info.type_encoding & ENCODING_MASK,
        newest_cycle_at_collection=capture.newest_cycle,
        cycles_behind=capture.newest_cycle - capture.cycle,
        decoder_version=DECODER_VERSION,
        problems=list(notes),
    )

    header = capture.header
    if header is not None:
        record.header_available = True
        record.recipe_number = header.recipe_number
        record.job_info = header.job_info
        record.reference_cycle_number = header.reference_cycle
        record.recipe_title = header.recipe_title
        record.slot_id = header.slot_id
        record.dispense_volume_setpoint_ul = header.volume_setpoint_ul
        if header.volume_measured_ml_x1000 is not None:
            record.dispense_volume_measured_ml = header.volume_measured_ml_x1000 / ML_X1000_PER_ML
        record.confirmation_pct = header.confirmation_pct
        if header.ready_pressure_cpsi is not None:
            record.ready_pressure_psi = header.ready_pressure_cpsi / CPSI_PER_PSI
        if header.reference_ready_pressure_cpsi is not None:
            record.reference_ready_pressure_psi = header.reference_ready_pressure_cpsi / CPSI_PER_PSI
        if header.time != info.time:
            record.problems.append(f"DISPENSE_INFO time {header.time}, profile info time {info.time}")
        record.problems += header.problems
    elif capture.header_problem is not None:
        record.problems.append(capture.header_problem)

    for pull, samples in zip(capture.traces, decoded):
        if samples is None:
            record.blank_traces.append(pull.trace)
        for chunk in pull.chunks:
            record.retries += chunk.attempts - 1
            if chunk.failed:
                record.failed_chunks += 1
            # a repair only counts once its trace made it into the CSV
            if chunk.repair is not None and samples is not None:
                record.reconstructed = True
                record.repairs.append({"trace": pull.trace, "start": chunk.start, **asdict(chunk.repair)})
    return record


def profile_path(serial, cycle):
    return f"{PROFILES_PATH}/{serial}/{cycle}"


def flaws(dispense):
    """
    How far a dispense row is from clean, for comparing two collections of one cycle. Lower is better.
    """
    return len(dispense["blank_traces"]), dispense["reconstructed"]


def already_clean(collected, cycle):
    saved = collected.get(cycle)
    return saved is not None and flaws(saved) == (0, False)


class WriteError(Exception):
    pass


def write_failure(writer, error):
    return f"{type(writer).__name__}: {type(error).__name__}: {error}"


def render(capture, writers, collected):
    """
    Store the profile and the dispense row for a capture. A clean result never gets replaced by a worse one of
    the same cycle, going by what collected remembers for this pump. Returns the dispense row and whether it was
    written.
    """
    decoded, notes = decode_traces(capture)
    record = dispense_record(capture, decoded, notes)

    saved = collected.get(capture.cycle)
    if saved is not None and flaws(saved) < flaws(asdict(record)):
        return record, False

    # each writer gets the profile first, so its row never points at a profile that isn't there, and links to its
    # own copy. one writer failing doesn't stop the others, but the cycle is only remembered once all of them
    # worked, so the next run tries again
    frame = profile_frame(asdict(capture.info), decoded)
    failures = []
    for writer in writers:
        try:
            record.trace_object_uri = writer.write_obj(profile_path(capture.pump.serial, capture.cycle), frame)
            record.write(writer)
        except Exception as error:
            failures.append(write_failure(writer, error))
    if failures:
        raise WriteError("; ".join(failures))

    collected.set(capture.cycle, record.blank_traces, record.reconstructed)
    return record, True


def write_pump(pump, address, writers):
    record = pump_record(pump, address)
    failures = []
    for writer in writers:
        try:
            record.write(writer)
        except Exception as error:
            failures.append(write_failure(writer, error))
    if failures:
        raise WriteError("; ".join(failures))
    return record


def main():
    if len(sys.argv) != 2:
        print("usage: python render.py <capture directory>")
        return 2
    directory = os.path.abspath(sys.argv[1])
    capture = Capture.read_raw(directory)

    out = directory                 # see file_writer.py header for format
    for _ in range("<out>/captures/<serial>/<cycle>/<run time>".count("/")):
        out = os.path.dirname(out)
    record, written = render(capture, [FileDBWriter(out)], CollectedCycles(out, capture.pump.serial))

    state = "written" if written else "kept the cleaner one already there"
    print(f"  {capture.pump.serial} {capture.cycle}, {state}")
    if record.blank_traces:
        print(f"  blank traces {record.blank_traces}")
    if record.reconstructed:
        print("  RECONSTRUCTED")
    return 0 if not record.blank_traces else 1


if __name__ == "__main__":
    sys.exit(main())
