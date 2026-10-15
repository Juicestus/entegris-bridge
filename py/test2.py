"""
Test 2. Check the two fixes from the last run: DISPENSE_INFO with a recipe number, and a pump error that comes back
behind a nonzero gateway code being reported as the pump's answer.
"""

import struct
import sys
import time

from ncs_client import NCSClient, NCSError
from pump_client import PumpClient, PumpTransportError
from pump_probe import code_name, print_rows
from pump_protocol import PumpCommand, PumpReturnCode
from pump_structs import DISPENSE_INFO_LEN, MOST_RECENT_DISPENSE, dispense_info_known_rows, dispense_info_request
from util import dump_bytes

# 2:10.25.85.52:8888:COM8
HOST = "10.25.85.52"
PORT = 8888
PUMP_COM = 8
PUMP_ADDR = 2

COMMAND_GAP_SEC = 0.2

# a bounds error carries the error code again and the index of the request field that was out of bounds
BOUNDS_ERROR_FORMAT = "<HH"


def send(pump, cmd, data):
    """
    Send one command and print its return code and raw data. Returns the reply, or None with the reason printed.
    """
    time.sleep(COMMAND_GAP_SEC)
    try:
        reply = pump.command(cmd, data)
    except PumpTransportError as error:
        print(f"  gateway returned {code_name(error.gateway_rc)}")
        return None
    except Exception as error:
        print(f"  {type(error).__name__}: {error}")
        return None

    print(f"  pump returned {code_name(reply.rc)}")
    dump_bytes("  data", reply.data)
    return reply


def main():
    print(f"NCS on {HOST}:{PORT}, pump address {PUMP_ADDR} on COM{PUMP_COM}")

    ncs = NCSClient(HOST, PORT)
    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except NCSError as error:
        print(f"  GET_VERSION -> {type(error).__name__}: {error}")
        print("\n  NCS not answering")
        return 1

    pump = PumpClient(ncs, PUMP_COM, PUMP_ADDR)

    print(f"\n * DISPENSE_INFO, recipe {MOST_RECENT_DISPENSE}\n")
    reply = send(pump, PumpCommand.DISPENSE_INFO, dispense_info_request(MOST_RECENT_DISPENSE))
    recipe_answered = reply is not None and reply.rc == PumpReturnCode.GOOD
    if recipe_answered:
        if len(reply.data) != DISPENSE_INFO_LEN:
            print(f"  LENGTH MISMATCH: {len(reply.data)} bytes, expected {DISPENSE_INFO_LEN}")
        print_rows(dispense_info_known_rows(reply.data))

    # the request the old probe sent, which the pump should refuse with a bounds error on the recipe number
    print("\n * DISPENSE_INFO, no data\n")
    reply = send(pump, PumpCommand.DISPENSE_INFO, b"")
    pump_answered = reply is not None
    if pump_answered and len(reply.data) >= struct.calcsize(BOUNDS_ERROR_FORMAT):
        error_code, field_index = struct.unpack_from(BOUNDS_ERROR_FORMAT, reply.data)
        print(f"  error code {error_code}, field {field_index}")

    print("\n * VERDICT\n")
    if recipe_answered:
        print("  Fix A PASSED")
    else:
        print("  Fix A FAILED")
    if pump_answered and reply.rc != PumpReturnCode.GOOD:
        print(f"  Fix B PASSED, {code_name(reply.rc)}")
    elif pump_answered:
        print("  Fix B NOT EXERCISED, no data was accepted")
    else:
        print("  Fix B FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
