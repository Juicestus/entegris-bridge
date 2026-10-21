"""
Decode a directory saved by test3.py into a CSV shaped like the vendor's profile export. Reads only files, so it
runs anywhere with no NCS and no pump.
"""

import json
import os
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from capture_format import INFO_FILE_NAME, WORD_LEN, trace_file_name
from profile_decode import decode, to_signed
from pump_structs import COMPRESSED_ENCODING, ENCODING_MASK, PROFILE_TRACE_NAMES

MS_PER_SEC = 1000
TIME_DECIMALS = 3


@dataclass
class TraceColumn:
    title: str
    signed: bool
    scale: int          # raw value divided by this gives the column's unit
    decimals: int


# in trace number order. titles and scaling are copied from an export this same pump made through the vendor
# software, so the two files can be compared directly
TRACE_COLUMNS = [
    TraceColumn("Dispense Pressure (psi)", True, 100, 2),
    TraceColumn("Fill Pressure (psi)", True, 100, 2),
    TraceColumn("Dispense Position (ml)", False, 1000, 3),
    TraceColumn("Fill Position (ml)", False, 1000, 3),
    TraceColumn("Dispense Current (mA)", True, 1, 0),
    TraceColumn("System Status (code)", False, 1, 0),
    TraceColumn("Valves Values (bit field)", False, 1, 0),
    TraceColumn("Flow Sensor (ml/s)", False, 1000, 3),
    TraceColumn("N2 Supply (psi)", True, 100, 2),
    TraceColumn("Vacuum Supply (psi)", True, 100, 2),
]
TIME_TITLE = "Time(sec)"


def scaled_text(raw, column):
    """
    One raw sample as text in its column's unit.
    """
    value = raw
    if column.signed:
        value = to_signed(raw)
    if column.scale == 1:
        return str(value)
    return f"{value / column.scale:.{column.decimals}f}"


def seconds_text(sample, info):
    """
    Time of a sample relative to the dispense trigger, which comes after a stretch of recording before it.
    """
    ms = (sample - info["start_cycle"]) * info["sample_interval"]
    return f"{ms / MS_PER_SEC:.{TIME_DECIMALS}f}"


def decode_trace(directory, trace, info):
    """
    The decoded samples of one trace, or None if they can't be trusted. Prints what happened either way.
    """
    column = TRACE_COLUMNS[trace]
    expected_words = info["encoded_size"][trace]
    expected_samples = info["unencoded_size"]
    print(f"\n * trace {trace} {PROFILE_TRACE_NAMES[trace]}\n")

    path = os.path.join(directory, trace_file_name(trace))
    if not os.path.exists(path):
        print(f"  NOT DECODED, {path} missing")
        return None
    with open(path, "rb") as file:
        raw = file.read()

    if len(raw) % WORD_LEN != 0:
        print(f"  NOT DECODED, {len(raw)} bytes, odd length")
        return None
    words = list(struct.unpack(f"<{len(raw) // WORD_LEN}H", raw))
    if len(words) != expected_words:
        print(f"  {len(words)} words, expected {expected_words}")

    notes = []
    try:
        samples = decode(words, expected_samples, notes)
    except ValueError as error:
        print(f"  NOT DECODED, {error}")
        return None
    finally:
        # a reason to distrust one delta, not the whole trace, so the column is kept
        for note in notes:
            print(f"  WARNING: {note}")

    if len(samples) != expected_samples:
        print(f"  NOT DECODED, {len(samples)} samples, expected {expected_samples}")
        return None

    print(f"  OK, {scaled_text(samples[0], column)} ... {scaled_text(samples[-1], column)}")
    return samples


def header_lines(info, missing):
    """
    The lines above the column titles, saying what was captured.
    """
    trigger = datetime.fromtimestamp(info["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f'CYCLENUM,"{info["cycle"]}"',
        f'TIMESTAMP,"{trigger}","{info["time"]}"',
        f'SAMPLEINTERVAL_MS,"{info["sample_interval"]}"',
        f'TYPE_ENCODING,"{info["type_encoding"]}"',
        f'SAMPLES,"{info["unencoded_size"]}"',
    ]
    # in seconds from the trigger like the vendor's header, with the raw sample number after
    boundaries = [
        ("STARTOFCYCLE_SEC", "start_cycle"),
        ("DISPENSEEND_SEC", "dispense_end"),
        ("FILTRATIONEND_SEC", "end_filtration"),
        ("FILLEND_SEC", "fill_end"),
        ("ENDOFCYCLE_SEC", "end_cycle"),
    ]
    for label, key in boundaries:
        lines.append(f'{label},"{seconds_text(info[key], info)}","{info[key]}"')
    if missing:
        titles = ", ".join(TRACE_COLUMNS[trace].title for trace in missing)
        lines.append(f'NOT_DECODED,"{titles}"')
    lines.append('"END OF HEADER"')
    return lines


def write_csv(path, info, decoded):
    """
    Write the header, the column titles and one row per sample, leaving a trace that didn't decode blank.
    """
    missing = [trace for trace in range(len(TRACE_COLUMNS)) if decoded[trace] is None]
    titles = [TIME_TITLE] + [column.title for column in TRACE_COLUMNS]

    with open(path, "w") as file:
        for line in header_lines(info, missing):
            file.write(line + "\n")
        # the vendor's title row ends with a comma and its data rows don't
        file.write(",".join(f'"{title}"' for title in titles) + ",\n")

        for sample in range(info["unencoded_size"]):
            cells = [seconds_text(sample, info)]
            for trace, column in enumerate(TRACE_COLUMNS):
                if decoded[trace] is None:
                    cells.append("")
                else:
                    cells.append(scaled_text(decoded[trace][sample], column))
            file.write(",".join(cells) + "\n")


def main():
    if len(sys.argv) != 2:
        print("usage: python decode.py <capture directory>")
        return 2
    directory = sys.argv[1]

    with open(os.path.join(directory, INFO_FILE_NAME)) as file:
        info = json.load(file)

    encoding = info["type_encoding"] & ENCODING_MASK
    print(f"  cycle {info['cycle']}, {info['unencoded_size']} samples, encoding {encoding}")
    if encoding != COMPRESSED_ENCODING:
        print(f"\n * VERDICT\n\n  FAILED, unknown encoding {encoding}")
        return 1

    decoded = []
    for trace in range(len(TRACE_COLUMNS)):
        decoded.append(decode_trace(directory, trace, info))

    path = os.path.join(directory, f"decoded_{info['cycle']}.csv")
    write_csv(path, info, decoded)

    failed = [trace for trace in range(len(TRACE_COLUMNS)) if decoded[trace] is None]
    print("\n * VERDICT\n")
    print(f"  {path}")
    if failed:
        print(f"  FAILED, blank traces {failed}")
        return 1
    print("  PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
