import sys

import config
from ncs_client import ConnectError, NCSClient, NCSError, ReturnCodeError
from ncs_protocol import COM_PORT_COUNT, LOGICAL_COM1, lserial
from util import PRINTABLE_MAX, PRINTABLE_MIN


def yes_no(value):
    return "YES" if value else "NO"


def print_settings():
    """
    Print the settings in use, so a log someone sends back explains itself.
    """
    print(
        "Settings:\n"
        f"  host:              {config.HOST}\n"
        f"  port:              {config.PORT}\n"
        f"  timeout_ms:        {config.TIMEOUT_MS}\n"
        f"  socket_margin_ms:  {config.SOCKET_MARGIN_MS}"
    )


def print_status(what, error):
    print(f"  {what} -> {type(error).__name__}: {error}")


def print_interface(com, info):
    """
    One row of the port table.
    """
    line = (f"  COM{com}  {yes_no(info.enabled):<3}  baud {info.baud:6d}  served {info.served:8d}"
            f"  tail {len(info.tail):3d}")

    # printable chars out of the tail until we know the layout
    if info.tail:
        printable = ""
        for value in info.tail:
            if PRINTABLE_MIN <= value < PRINTABLE_MAX:
                printable += chr(value)
        line += f'  "{printable}"'
    print(line)


def main():
    print("NCS probe. This only talks to the NCS box itself, nothing is sent to a pump.\n")
    print_settings()

    ncs = NCSClient(config.HOST, config.PORT)

    # NCS info

    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except ConnectError as error:
        # no point making more attempts against a box we can't reach
        print_status("GET_VERSION", error)
        print(f"\n  Could not reach {config.HOST}:{config.PORT}, giving up.")
        return 1
    except NCSError as error:
        # we got through, so keep going, the rest of the dumps are still worth having
        print_status("GET_VERSION", error)

    print("\n * GET_NAME\n")
    try:
        print(f"  name:    {ncs.get_name()}")
    except NCSError as error:
        print_status("GET_NAME", error)

    print("\n * GET_INTERFACES\n")
    n_iface = 0
    try:
        n_iface = ncs.get_interfaces()
    except NCSError as error:
        print_status("GET_INTERFACES", error)

    n_com = COM_PORT_COUNT
    if 1 <= n_iface <= COM_PORT_COUNT:
        n_com = n_iface
    else:
        print(f"  (count unusable, querying {n_com} anyway)")

    # port table

    print(f"\n * QUERY_INTERFACE, logical_com1 = {LOGICAL_COM1}\n")
    for com in range(1, n_com + 1):
        try:
            print_interface(com, ncs.query_interface(com))
        except ReturnCodeError as error:
            # UNKNOWN_SERIAL_PORT here would mean the COM to logical serial mapping is wrong
            print(f"  COM{com} (lserial {lserial(com)})  rc {int(error.rc)}")
        except NCSError as error:
            print_status("QUERY_INTERFACE", error)

    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
