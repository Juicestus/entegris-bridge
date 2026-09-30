import struct
from dataclasses import dataclass
from enum import IntEnum

ENDIAN = "<"        # confirmed by Entegris
LOGICAL_COM1 = 0

# request and response headers are the same size and shape, only the meaning of the fields differs
HEADER_FORMAT = ENDIAN + "HHHHHLL"
HEADER_LEN = struct.calcsize(HEADER_FORMAT)
PROTOCOL_HEADER_LEN = 18

# catch a typo in the format string as soon as this is imported
assert HEADER_LEN == PROTOCOL_HEADER_LEN

MAX_PACKET_SIZE = 4096
MAX_SIZE_FIELD = 0xFFFF

COM_PORT_COUNT = 8

# the spec doesn't say how wide the GET_INTERFACES count is
COUNT32_FORMAT = ENDIAN + "L"
COUNT16_FORMAT = ENDIAN + "H"

# no limit in the spec, only here to reject garbage
MAX_INTERFACE_COUNT = 64

# enabled, baudrate, requests served. the two device name fields after this have no stated layout
INTERFACE_FIXED_FORMAT = ENDIAN + "llL"
INTERFACE_FIXED_LEN = struct.calcsize(INTERFACE_FIXED_FORMAT)


class Command(IntEnum):
    SEND_PACKET = 0         # tunnels a pump frame, 1b
    GET_INTERFACES = 1
    GET_NAME = 2
    GET_VERSION = 3
    QUERY_INTERFACE = 4
    LOCK_INTERFACE = 5      # never sent
    UNLOCK_INTERFACE = 6    # never sent


class ReturnCode(IntEnum):
    NO_ERR = 0
    INTERNAL = 1001
    UNKNOWN_COMMAND = 1002
    MALFORMED_PACKET = 1003
    UNKNOWN_SERIAL_PORT = 1004          # also what a wrong logical serial mapping gives us
    DISABLED_SERIAL_PORT = 1005
    INPUT_INVALID_CRC = 1006            # our frame's CRC, checked before it hits the bus
    EXTRA_DATA = 1007
    INVALID_SERIAL_DATA = 1008
    LOCKED_INTERFACE = 1010             # no 1009 in the spec
    NOT_LOCKED_INTERFACE = 1011
    INVALID_LOCK_CODE = 1012
    SERIAL_COMMAND_FAILED = 1013
    SERIAL_SHORT_READ = 1014
    SERIAL_READ_TIMEOUT = 1015
    SERIAL_OUTPUT_INVALID_CRC = 1016    # pump's reply CRC


@dataclass
class Response:
    raw: bytes      # everything received, header included
    rc: int         # a ReturnCode when we know the value, otherwise the plain number
    body: bytes     # raw past the header


def lserial(com):
    """
    Logical serial number for a COM port as labeled on the box.
    """
    return com - 1 + LOGICAL_COM1


def build_request(cmd, *, timeout_ms, lserial=0, ctrl=0, data=b""):
    """
    Build a complete request packet, header followed by data.
    """
    # locking affects other users of the same serial bus, so it must not be possible to do by accident
    if cmd in (Command.LOCK_INTERFACE, Command.UNLOCK_INTERFACE):
        raise ValueError(f"refusing to build {Command(cmd).name}")

    # size counts the header too
    size = HEADER_LEN + len(data)
    if size > MAX_PACKET_SIZE:
        raise ValueError(f"packet of {size} bytes is over the maximum of {MAX_PACKET_SIZE}")
    if size > MAX_SIZE_FIELD:
        raise ValueError(f"packet of {size} bytes does not fit in the size field")

    # the lock code and reserved fields are always zero
    header = struct.pack(HEADER_FORMAT, int(cmd), size, timeout_ms, ctrl, lserial, 0, 0)
    return header + data


def parse_response(raw):
    """
    Split a received message into return code and body. A nonzero return code is not an error here.
    """
    if len(raw) < HEADER_LEN:
        raise ValueError(f"response is {len(raw)} bytes, shorter than the header")
    if len(raw) > MAX_PACKET_SIZE:
        raise ValueError(f"response is over the maximum of {MAX_PACKET_SIZE} bytes")

    rc_value, size = struct.unpack_from(HEADER_FORMAT, raw)[:2]

    # this check is what makes a wrong byte order or size assumption fail loudly
    if size != len(raw):
        raise ValueError(f"size field says {size} but we received {len(raw)} bytes")

    # a code missing from the spec is still an answer from the box, so keep the number
    try:
        rc = ReturnCode(rc_value)
    except ValueError:
        rc = rc_value

    return Response(raw=bytes(raw), rc=rc, body=bytes(raw[HEADER_LEN:]))


def decode_string(body):
    """
    Text out of a fixed width null padded field.
    """
    return body.split(b"\x00", 1)[0].decode("ascii", errors="replace")
