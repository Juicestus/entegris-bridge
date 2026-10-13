"""
Read the firmware information from one pump through the NCS box
"""

import sys
from enum import IntEnum

from ncs_client import NCSClient, NCSError
from pump_client import PumpClient, PumpError, PumpReturnCodeError, PumpTransportError
from pump_protocol import PRODUCT_QUALIFIER

# 2:10.25.85.52:8888:COM8
HOST = "10.25.85.52"
PORT = 8888
PUMP_COM = 8
PUMP_ADDR = 2


def print_status(what, error):
    print(f"  {what} -> {type(error).__name__}: {error}")


def code_name(rc):
    """
    Name of a return code, or a note that it isn't one we know.
    """
    if isinstance(rc, IntEnum):
        return f"{int(rc)} {rc.name}"
    return f"{rc} (not a code we know)"


def main():
    print("Pump probe. This only reads, no write or control command can be sent.")
    print(f"NCS on {HOST}:{PORT}, pump address {PUMP_ADDR} on COM{PUMP_COM}")

    ncs = NCSClient(HOST, PORT)

    # no point sending pump traffic through a box that isn't answering for itself
    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except NCSError as error:
        print_status("GET_VERSION", error)
        print(f"\n  The NCS on {HOST}:{PORT} is not answering, giving up.")
        return 1

    print(f"\n * FIRMWAREINFO, pump {PUMP_ADDR} on COM{PUMP_COM}\n")
    pump = PumpClient(ncs, PUMP_COM, PUMP_ADDR)
    try:
        info = pump.get_firmware_info()
    except PumpTransportError as error:
        # the gateway code says why, a pump timeout here usually means the address or COM port is wrong
        print(f"  gateway return code {code_name(error.gateway_rc)}")
        return 1
    except PumpReturnCodeError as error:
        print(f"  pump return code {code_name(error.rc)}")
        return 1
    except (PumpError, NCSError) as error:
        print_status("FIRMWAREINFO", error)
        return 1

    print(f"  ProductQualifier:  {info.product_qualifier}")
    print(f"  Comms_Version:     {info.comms_version}")
    print(f"  MajorVersion:      {info.major_version}")
    print(f"  ModuleType:        {info.module_type}")
    print(f"  MinorVersion:      {info.minor_version}")
    print(f"  ProductCode:       {info.product_code}")
    print(f"  MapSize:           {info.map_size}")
    print(f"  CRC:               {info.crc}")
    print()

    if info.product_qualifier != PRODUCT_QUALIFIER:
        print(f"  ProductQualifier does NOT match, got {info.product_qualifier}, expected {PRODUCT_QUALIFIER}")
        return 1

    print(f"  ProductQualifier matches the expected {PRODUCT_QUALIFIER}")
    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
