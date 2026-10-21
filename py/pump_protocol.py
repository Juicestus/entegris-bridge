import struct
from dataclasses import dataclass
from enum import IntEnum

from crc16 import CRC_LEN, append_crc, is_valid

ENDIAN = "<"        # same as the gateway layer

# address, command or response code, size
FRAME_HEADER_FORMAT = ENDIAN + "BBH"
FRAME_HEADER_LEN = struct.calcsize(FRAME_HEADER_FORMAT)
FRAME_CMD_OFFSET = 1
MIN_FRAME_LEN = FRAME_HEADER_LEN + CRC_LEN

# the size field counts the whole frame, header and CRC included
MAX_FRAME_SIZE = 0xFFFF

MIN_ADDR = 1
MAX_ADDR = 63

# the data portion must always be a whole number of words
DATA_ALIGN = 2


class PumpCommand(IntEnum):
    """
    Every command this program is allowed to send, read commands only.
    """
    # these pumps run production, and the read and write forms of a command are usually adjacent numbers,
    # so a guessed value can easily land on a write
    GET_SVIDS = 21
    GET_ALARMS = 24
    READ_RT_TRACES = 26
    SERIALNUMBERS = 30
    GET_PROFILE_TRACE = 32
    GET_PROFILE_TRACE_INFO = 33
    VOLUMECOUNTERS = 50
    VERSION = 52
    FIRMWAREINFO = 54
    CYCLECOUNTERS = 60
    RECHARGE_PARAMETERS = 70
    RECIPE = 74
    RTSTATUS = 100
    PRESSURE_STATUS = 104
    UTILITY_STATUS = 106
    CS_RTSTATUS = 110
    DC_LIMITS = 153
    DISPENSE_INFO = 155
    DC_STATUS = 158
    SKIPSTEPINFO = 166
    LAST_DIS_INFO_2 = 167
    MINI_STATUS = 168
    MINI_STATUS_SMALL = 169


class PumpReturnCode(IntEnum):
    GOOD = 0
    SIZE_ERROR = 1
    BAD_CRC = 3
    BUSY = 6
    BOUNDS_ERROR = 10       # data carries the error code and which request field was out of bounds
    SVID_LIST_TOO_LARGE = 12
    UNKNOWN_SVID = 13
    # named from what we saw, not from any document. it's the answer for a profile cycle the pump doesn't hold
    PROFILE_NOT_HELD = 29
    BAD_PASSWORD = 32
    NOT_SUPPORTED = 36


@dataclass
class PumpResponse:
    raw: bytes      # the whole reply frame, CRC included
    addr: int       # the pump that answered
    rc: int         # a PumpReturnCode when we know the value, otherwise the plain number
    data: bytes     # between the header and the CRC


FIRMWARE_INFO_FORMAT = ENDIAN + "HHHHHHHH"
FIRMWARE_INFO_LEN = struct.calcsize(FIRMWARE_INFO_FORMAT)

# a fixed marker, reading it back means the parse lines up
PRODUCT_QUALIFIER = 1234


@dataclass
class FirmwareInfo:
    product_qualifier: int
    comms_version: int
    major_version: int
    module_type: int
    minor_version: int
    product_code: int
    map_size: int       # deprecated
    crc: int            # deprecated

    @classmethod
    def parse(cls, data):
        """
        Decode the data portion of a FIRMWAREINFO reply.
        """
        # a different length means this isn't the structure we think it is, so don't guess at the fields
        if len(data) != FIRMWARE_INFO_LEN:
            raise ValueError(f"firmware info is {len(data)} bytes, expected {FIRMWARE_INFO_LEN}")
        return cls(*struct.unpack(FIRMWARE_INFO_FORMAT, data))


def allowed_command(cmd):
    """
    The PumpCommand for a command number, raising ValueError if it is not in the allowlist.
    """
    try:
        return PumpCommand(cmd)
    except ValueError:
        raise ValueError(f"refusing pump command {cmd}, it is not in the allowlist") from None


def build_frame(addr, cmd, data=b""):
    """
    Build a complete pump frame, header followed by data and CRC.
    """
    command = allowed_command(cmd)

    if not MIN_ADDR <= addr <= MAX_ADDR:
        raise ValueError(f"pump address {addr} is outside {MIN_ADDR} to {MAX_ADDR}")
    if len(data) % DATA_ALIGN != 0:
        raise ValueError(f"pump data is {len(data)} bytes, it must be a whole number of words")

    size = FRAME_HEADER_LEN + len(data) + CRC_LEN
    if size > MAX_FRAME_SIZE:
        raise ValueError(f"pump frame of {size} bytes does not fit in the size field")

    header = struct.pack(FRAME_HEADER_FORMAT, addr, int(command), size)
    return append_crc(header + data)


def parse_frame(raw, expect_addr):
    """
    Split a reply frame into address, return code and data. A nonzero return code is not an error here.
    """
    if len(raw) < MIN_FRAME_LEN:
        raise ValueError(f"pump reply is {len(raw)} bytes, shorter than the smallest frame")

    addr, rc_value, size = struct.unpack_from(FRAME_HEADER_FORMAT, raw)
    if size != len(raw):
        raise ValueError(f"pump size field says {size} but we received {len(raw)} bytes")

    # checked before the address, since on a bad CRC the address byte can't be trusted either
    if not is_valid(raw):
        raise ValueError("pump reply failed the CRC check")
    if addr != expect_addr:
        raise ValueError(f"reply came from pump {addr}, we asked pump {expect_addr}")

    # a code missing from our list is still an answer from the pump, so keep the number
    try:
        rc = PumpReturnCode(rc_value)
    except ValueError:
        rc = rc_value

    return PumpResponse(raw=bytes(raw), addr=addr, rc=rc, data=bytes(raw[FRAME_HEADER_LEN:size - CRC_LEN]))
