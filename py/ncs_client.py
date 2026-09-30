import socket
import struct
from dataclasses import dataclass

from ncs_protocol import (
    COUNT16_FORMAT,
    COUNT32_FORMAT,
    INTERFACE_FIXED_FORMAT,
    INTERFACE_FIXED_LEN,
    MAX_INTERFACE_COUNT,
    MAX_PACKET_SIZE,
    Command,
    ReturnCode,
    build_request,
    decode_string,
    lserial,
    parse_response,
)
from util import dump_bytes

TIMEOUT_MS          = 5000
SOCKET_MARGIN_MS    = 1000
MS_PER_SEC          = 1000

class NCSError(Exception):
    """
    Base for everything the client raises, so a caller can catch one type per command.
    """


class NetworkError(NCSError):
    """
    Connected, but the bytes did not make it there and back.
    """


class ConnectError(NetworkError):
    """
    Never got a connection to the box at all.
    """


class ProtocolError(NCSError):
    """
    Got bytes, they do not parse.
    """


class ReturnCodeError(NCSError):
    """
    Parsed fine, but the box answered with a return code other than NO_ERR.
    """
    def __init__(self, cmd, rc):
        super().__init__(f"{Command(cmd).name} answered with return code {int(rc)}")
        self.cmd = cmd
        self.rc = rc


@dataclass
class InterfaceInfo:
    enabled: int
    baud: int
    served: int     # requests served, diff this to see MMI traffic on the bus
    tail: bytes     # the two device name fields, layout unknown until we see bytes


class NCSClient:
    def __init__(self, host: str, port: int, timeout_ms=TIMEOUT_MS):
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms

    def transact(self, cmd, *, lserial=0, ctrl=0, data=b"", check_rc=True):
        """
        Open a connection, send one request, wait for the response and close the connection. Both directions
        are hex dumped. Raises ReturnCodeError on a nonzero return code unless check_rc is off.
        """
        packet = build_request(cmd, timeout_ms=self.timeout_ms, lserial=lserial, ctrl=ctrl, data=data)
        dump_bytes("-->", packet)

        # socket timeout has to outlast the header's timeout field, or we give up
        # before the NCS can tell us it timed out on the serial side (1b)
        socket_timeout = (self.timeout_ms + SOCKET_MARGIN_MS) / MS_PER_SEC 

        try:
            sock = socket.create_connection((self.host, self.port), timeout=socket_timeout)
        except OSError as error:
            raise ConnectError(f"could not connect to {self.host}:{self.port}: {error}") from error

        received = b""
        failure = None
        with sock:
            try:
                sock.sendall(packet)

                # read one byte past the max so an oversize response fails the parse instead of being cut short
                while len(received) <= MAX_PACKET_SIZE:
                    try:
                        chunk = sock.recv(MAX_PACKET_SIZE + 1 - len(received))
                    except socket.timeout:
                        # the spec doesn't say who closes the connection, so the box could be waiting on us
                        if received:
                            break
                        raise
                    if not chunk:
                        break       # peer closed
                    received += chunk
            except OSError as error:
                failure = error

        # dump even on failure, from a run we can't watch the dumps are the only evidence we get back
        dump_bytes("<--", received)
        if failure is not None:
            raise NetworkError(f"{Command(cmd).name} failed talking to {self.host}:{self.port}: {failure}") from failure

        try:
            response = parse_response(received)
        except ValueError as error:
            raise ProtocolError(f"{Command(cmd).name} response did not parse: {error}") from error

        if check_rc and response.rc != ReturnCode.NO_ERR:
            raise ReturnCodeError(cmd, response.rc)
        return response

    def get_version(self):
        """
        Ask the box for its version string.
        """
        response = self.transact(Command.GET_VERSION)
        return decode_string(response.body)

    def get_name(self):
        """
        Ask the box for its name.
        """
        response = self.transact(Command.GET_NAME)
        return decode_string(response.body)

    def get_interfaces(self):
        """
        Ask the box how many logical serial interfaces it has.
        """
        response = self.transact(Command.GET_INTERFACES)
        body = response.body

        # decode as 32 and 16 bit and print both, it's evidence either way
        wide = None
        narrow = None
        if len(body) >= struct.calcsize(COUNT32_FORMAT):
            wide = struct.unpack_from(COUNT32_FORMAT, body)[0]
            print(f"  GET_INTERFACES as UINT32:  {wide}")
        if len(body) >= struct.calcsize(COUNT16_FORMAT):
            narrow = struct.unpack_from(COUNT16_FORMAT, body)[0]
            print(f"  GET_INTERFACES as UINT16: {narrow}")

        if wide is not None and 1 <= wide <= MAX_INTERFACE_COUNT:
            return wide
        if narrow is not None and 1 <= narrow <= MAX_INTERFACE_COUNT:
            return narrow

        raise ProtocolError(f"could not decode an interface count from {len(body)} bytes")

    def query_interface(self, com):
        """
        Ask the box for the state of one COM port, numbered as labeled on the box.
        """
        # watch for UNKNOWN_SERIAL_PORT here, it means the COM to logical serial mapping is wrong
        response = self.transact(Command.QUERY_INTERFACE, lserial=lserial(com))

        body = response.body
        if len(body) < INTERFACE_FIXED_LEN:
            raise ProtocolError(f"interface info is {len(body)} bytes, need at least {INTERFACE_FIXED_LEN}")

        enabled, baud, served = struct.unpack_from(INTERFACE_FIXED_FORMAT, body)
        return InterfaceInfo(enabled=enabled, baud=baud, served=served, tail=body[INTERFACE_FIXED_LEN:])
