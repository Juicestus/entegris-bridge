"""
SVIDs, single values read by number with GET_SVIDS, so a poll can fetch exactly the fields it wants in one trip
"""

import struct
from dataclasses import dataclass
from enum import Enum

from units import CC, CPSI, EPOCH, PERCENT, PSI, UL

ENDIAN = "<"
SVID_NUMBER_FORMAT = ENDIAN + "H"
STRING_LEN_FORMAT = ENDIAN + "H"


class SvidType(Enum):
    INT16 = "h"
    UINT16 = "H"
    LONG = "l"
    ULONG = "L"
    FLOAT = "f"
    STRING = None       # a byte count first, then that many bytes

    def size(self):
        """
        Size in bytes on the wire, or None for a string since it carries its own length.
        """
        if self.value is None:
            return None
        return struct.calcsize(ENDIAN + self.value)


@dataclass(frozen=True)
class Svid:
    name: str
    type: SvidType
    unit: str = None


# The reply has no tags or delimiters, so every value is found only by knowing the type of each one before it.
# One wrong type here misaligns everything after it into plausible looking garbage rather than an error.
# SVID numbers are published per firmware version and can change between revisions, so eventually this will be
# chosen by firmware version. One registry is enough for now.
REGISTRY = {
    769: Svid("FirmwareInfo.ProductQualifier", SvidType.UINT16),
    771: Svid("FirmwareInfo.MajorVersion", SvidType.UINT16),
    773: Svid("FirmwareInfo.MinorVersion", SvidType.UINT16),
    257: Svid("RtStatus.Status", SvidType.INT16),
    796: Svid("RtStatus.LastDispenseVol_uL", SvidType.UINT16, UL),
    1056: Svid("RtStatus.LastDispenseCycle", SvidType.ULONG),
    1058: Svid("RtStatus.ProfileCycle", SvidType.ULONG),
    1048: Svid("RtStatus.NewestAlarmNum", SvidType.ULONG),
    1025: Svid("TimeNow.Time", SvidType.ULONG, EPOCH),
    269: Svid("PressureStatus.Dis_pressure_cPSI", SvidType.INT16, CPSI),
    270: Svid("PressureStatus.Fill_pressure_cPSI", SvidType.INT16, CPSI),
    287: Svid("UtilityStatus.N2Sensor_cPSI", SvidType.INT16, CPSI),
    288: Svid("UtilityStatus.VacSensor_cPSI", SvidType.INT16, CPSI),
    298: Svid("UtilityStatus.TempFillSensor_cC", SvidType.INT16, CC),
    299: Svid("UtilityStatus.TempDisSensor_cC", SvidType.INT16, CC),
    513: Svid("PressureStatus.Dis_pressure_PSI", SvidType.FLOAT, PSI),
    514: Svid("PressureStatus.Fill_pressure_PSI", SvidType.FLOAT, PSI),
    10: Svid("Version.Model", SvidType.STRING),
    11: Svid("Version.Version", SvidType.STRING),
    12: Svid("SystemNames.CustomerName", SvidType.STRING),
    13: Svid("SystemNames.FabName", SvidType.STRING),
    14: Svid("SystemNames.ToolName", SvidType.STRING),
    15: Svid("SystemNames.CoaterName", SvidType.STRING),
    16: Svid("SystemNames.NozzleName", SvidType.STRING),
    17: Svid("SystemNames.ResistName", SvidType.STRING),
    18: Svid("SystemNames.PumpName", SvidType.STRING),
    19: Svid("SystemNames.FluidName", SvidType.STRING),
    20: Svid("Recipe.RecipeTitle", SvidType.STRING),
    21: Svid("DispenseInfo.JobInfo", SvidType.STRING),
    # these two belong to DISPENSE_INFO, which needs an ENGINEER password, but GET_SVIDS needs none
    849: Svid("DispenseInfo.Confirmation", SvidType.UINT16, PERCENT),
    1071: Svid("DispenseInfo.Time", SvidType.ULONG, EPOCH),
}


@dataclass
class SvidValue:
    number: int
    svid: Svid
    value: object


def build_request(numbers):
    """
    Send data for GET_SVIDS asking for these SVID numbers, in this order.
    """
    # an SVID we don't know the type of would make the whole reply unreadable, so it can't be asked for
    data = b""
    for number in numbers:
        if number not in REGISTRY:
            raise ValueError(f"SVID {number} is not in the registry, its type is unknown")
        data += struct.pack(SVID_NUMBER_FORMAT, number)
    return data


def decode_string(raw):
    """
    Text out of a string value, cut at the first null.
    """
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def walk_reply(data, numbers):
    """
    Values for the requested SVID numbers in order, and how many bytes of the reply were left over.
    """
    values = []
    offset = 0
    for number in numbers:
        svid = REGISTRY[number]

        if svid.type == SvidType.STRING:
            len_size = struct.calcsize(STRING_LEN_FORMAT)
            if offset + len_size > len(data):
                raise ValueError(f"reply ran out at byte {offset} reading the length of SVID {number} {svid.name}")
            text_len = struct.unpack_from(STRING_LEN_FORMAT, data, offset)[0]
            offset += len_size

            if offset + text_len > len(data):
                raise ValueError(f"reply ran out at byte {offset} reading {text_len} bytes of SVID {number} "
                                 f"{svid.name}")
            value = decode_string(data[offset:offset + text_len])

            # the spec doesn't say whether an odd length string is padded out to a whole word. if it is, every
            # value after it comes out shifted by one byte, and the leftover count won't be zero
            offset += text_len
        else:
            size = svid.type.size()
            if offset + size > len(data):
                raise ValueError(f"reply ran out at byte {offset} reading SVID {number} {svid.name}")
            value = struct.unpack_from(ENDIAN + svid.type.value, data, offset)[0]
            offset += size

        values.append(SvidValue(number, svid, value))

    return values, len(data) - offset
