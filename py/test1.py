"""
Test 1. Run the pump probe again, with the gateway's requests served counters read before and after it, so the
traffic on every bus over that time can be compared with earlier runs.
"""

from util import deprecated
deprecated()

import sys
import time

import pump_probe
from ncs_client import NCSClient, NCSError
from ncs_protocol import COM_PORT_COUNT

# the same box and pump the probe talks to, so the two can't drift apart
HOST = pump_probe.HOST
PORT = pump_probe.PORT


def read_served(ncs):
    """
    Requests served on every COM port, None for a port that didn't answer.
    """
    served = {}
    for com in range(1, COM_PORT_COUNT + 1):
        try:
            served[com] = ncs.query_interface(com).served
        except NCSError as error:
            print(f"  COM{com} -> {type(error).__name__}: {error}")
            served[com] = None
    return served


def main():
    ncs = NCSClient(HOST, PORT)

    print("\n * served, before\n")
    before = read_served(ncs)
    start = time.monotonic()

    probe_result = pump_probe.main()

    elapsed = time.monotonic() - start
    print("\n * served, after\n")
    after = read_served(ncs)

    print(f"\n * served over {elapsed:.1f}s\n")
    for com in range(1, COM_PORT_COUNT + 1):
        if before[com] is None or after[com] is None:
            print(f"  COM{com}  not read")
            continue
        print(f"  COM{com}  {before[com]:10d} -> {after[com]:10d}  served {after[com] - before[com]:6d}")

    print("\n * VERDICT\n")
    if probe_result != 0:
        print("  FAILED, NCS not answering")
        return 1
    print("  DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
