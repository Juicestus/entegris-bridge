"""
Test connection to a pump and poll a number of SVIDs. 

Thise phase was successful... output from testing in docs/pump_probe.out
"""

import sys
import time
from dataclasses import dataclass, fields
from enum import IntEnum

import svid
from ncs_client import NCSClient, NCSError
from ncs_protocol import ReturnCode
from pump_client import PumpClient, PumpTransportError
from pump_protocol import PRODUCT_QUALIFIER, FirmwareInfo, PumpCommand, PumpReturnCode
from pump_structs import (
    ALARM_SEVERITY,
    DISPENSE_INFO_LEN,
    MOST_RECENT_ALARM,
    MOST_RECENT_DISPENSE,
    UTILITY_STATUS_LEN,
    CsRtStatus,
    CycleCounters,
    LastDispenseInfo2,
    PressureStatus,
    ProfileTraceInfo,
    RtStatus,
    SerialNumbers,
    SkipStepInfo,
    Version,
    VolumeCounters,
    alarm_request,
    dispense_info_known_rows,
    dispense_info_request,
    parse_alarms,
    profile_info_request,
    utility_status_known_rows,
)
from units import format_value
from util import dump_bytes

# 2:10.25.85.52:8888:COM8
HOST = "10.25.85.52"
PORT = 8888
PUMP_COM = 8
PUMP_ADDR = 2

# the serial bus is shared and the spec wants a gap between transactions. a pump that times out also stops
# answering for a while afterwards, so nothing is ever retried right after a timeout, and the next command waits
# longer rather than walking into the same silence
COMMAND_GAP_SEC = 0.2

# the NCS manual says a pump that timed out only answers with an abort until this long has passed. it says so in
# the SECS section, so it may not hold on every path, but waiting too long costs this once, and waiting too little
# can cost the rest of the run
TIMEOUT_RECOVERY_SEC = 10

# if the safe list is refused, this tells "SVIDs don't work" apart from "that list was too long"
SVID_SHORT_LIST = [769, 771, 773]

# the two whose own command needs a password, kept apart so a refusal caused by them can't hide whether the rest work
SVID_PASSWORD_PAIR = [849, 1071]
SVID_SAFE_LIST = [number for number in svid.REGISTRY if number not in SVID_PASSWORD_PAIR]

# not a real cycle. still asked about when neither status reply gave us one, so its raw reply is on record
FALLBACK_PROFILE_CYCLE = 0

LABEL_WIDTH = 34


@dataclass
class Step:
    """
    What happened to one command, for the summary at the end.
    """
    name: str
    rc: object = None           # the pump's return code, when a reply frame came back
    gateway_rc: object = None   # the gateway's return code, when the frame didn't make it to the pump and back
    error: str = None           # anything else that went wrong, including a failed decode
    mismatch: str = None        # a reply that decoded but wasn't the size we expected

    def outcome(self):
        """
        One line saying how this command went.
        """
        if self.gateway_rc is not None:
            return f"gateway {code_name(self.gateway_rc)}"
        if self.rc is None:
            return self.error
        if self.rc != PumpReturnCode.GOOD:
            return code_name(self.rc)
        if self.error is not None:
            return f"GOOD, {self.error}"
        return "GOOD"


def code_name(rc):
    """
    Name of a return code, or a note that it isn't one we know.
    """
    if isinstance(rc, IntEnum):
        return f"{int(rc)} {rc.name}"
    return f"{rc} (not a code we know)"


def print_rows(rows):
    for label, value, unit in rows:
        print(f"  {label + ':':<{LABEL_WIDTH}} {format_value(value, unit)}")


def send(pump, steps, cmd, data=b"", name=None):
    """
    Send one command, print its return code and raw data, and return the reply or None if none came back.
    """
    if name is None:
        name = cmd.name
    step = Step(name)
    steps.append(step)
    print(f"\n * {name} ({int(cmd)})\n")

    time.sleep(COMMAND_GAP_SEC)
    try:
        reply = pump.command(cmd, data)
    except PumpTransportError as error:
        step.gateway_rc = error.gateway_rc
        print(f"  gateway returned {code_name(error.gateway_rc)}")
        if error.gateway_rc == ReturnCode.SERIAL_READ_TIMEOUT:
            time.sleep(TIMEOUT_RECOVERY_SEC)
        return None, step
    except Exception as error:
        # anything at all, a failed command must never stop the commands after it
        step.error = f"{type(error).__name__}: {error}"
        print(f"  {step.error}")
        return None, step

    step.rc = reply.rc
    print(f"  pump returned {code_name(reply.rc)}")
    dump_bytes("  data", reply.data)
    return reply, step


def answered(reply):
    return reply is not None and reply.rc == PumpReturnCode.GOOD


def show_struct(step, struct_class, data):
    """
    Decode and print a fixed layout reply, flagging a length that doesn't match the layout.
    """
    try:
        decoded = struct_class.parse(data)
        if not decoded.length_matches():
            step.mismatch = f"{len(data)} bytes, expected {struct_class.length()}"
            print(f"  LENGTH MISMATCH: {step.mismatch}")
        print_rows(decoded.rows())
        return decoded
    except Exception as error:
        step.error = f"decode failed, {type(error).__name__}: {error}"
        print(f"  {step.error}")
        return None


def show_firmware(step, data):
    """
    Decode and print FIRMWAREINFO, which is already confirmed against real hardware.
    """
    try:
        info = FirmwareInfo.parse(data)
    except Exception as error:
        step.error = f"decode failed, {type(error).__name__}: {error}"
        print(f"  {step.error}")
        return

    for f in fields(info):
        print(f"  {f.name + ':':<{LABEL_WIDTH}} {getattr(info, f.name)}")
    if info.product_qualifier != PRODUCT_QUALIFIER:
        step.error = f"ProductQualifier is {info.product_qualifier}, expected {PRODUCT_QUALIFIER}"
        print(f"  {step.error}")


def show_svids(step, data, numbers):
    """
    Walk and print a GET_SVIDS reply. Returns whether every value was read with nothing left over.
    """
    try:
        values, leftover = svid.walk_reply(data, numbers)
    except Exception as error:
        step.error = f"decode failed, {type(error).__name__}: {error}"
        print(f"  {step.error}")
        return False

    for item in values:
        label = f"{item.number} {item.svid.name}"
        print(f"  {label + ':':<{LABEL_WIDTH}} {format_value(item.value, item.svid.unit)}")
    if leftover:
        step.mismatch = f"{leftover} bytes left over"
        print(f"  LEFTOVER: {step.mismatch}")
        return False
    return True


def run_svids(pump, steps):
    """
    Ask for the safe list, the short list if that is refused, then the password pair. Returns the summary answer.
    """
    reply, step = send(pump, steps, PumpCommand.GET_SVIDS, svid.build_request(SVID_SAFE_LIST),
                       "GET_SVIDS, safe list")
    if answered(reply):
        if show_svids(step, reply.data, SVID_SAFE_LIST):
            ordinary = "safe list GOOD"
        else:
            ordinary = f"safe list GOOD, {step.mismatch or step.error}"
    elif reply is None:
        ordinary = f"safe list {step.outcome()}"
    else:
        safe_refusal = step.outcome()
        reply, step = send(pump, steps, PumpCommand.GET_SVIDS, svid.build_request(SVID_SHORT_LIST),
                           "GET_SVIDS, firmware values only")
        if answered(reply):
            show_svids(step, reply.data, SVID_SHORT_LIST)
            ordinary = f"safe list {safe_refusal}, short list GOOD"
        else:
            ordinary = f"safe list {safe_refusal}, short list {step.outcome()}"

    # always asked, whatever happened above. the safe list doesn't contain these, and whether they get past the
    # password is a separate question from whether SVIDs work at all
    reply, step = send(pump, steps, PumpCommand.GET_SVIDS, svid.build_request(SVID_PASSWORD_PAIR),
                       "GET_SVIDS, password pair")
    if answered(reply):
        if show_svids(step, reply.data, SVID_PASSWORD_PAIR):
            pair = "pair GOOD"
        else:
            pair = f"pair GOOD, {step.mismatch or step.error}"
    else:
        pair = f"pair {step.outcome()}"

    return f"{ordinary}; {pair}"


def run_dispense_info(pump, steps):
    """
    Send DISPENSE_INFO for the most recent dispense. Returns the summary answer.
    """
    reply, step = send(pump, steps, PumpCommand.DISPENSE_INFO, dispense_info_request(MOST_RECENT_DISPENSE))
    if answered(reply):
        if len(reply.data) != DISPENSE_INFO_LEN:
            step.mismatch = f"{len(reply.data)} bytes, expected {DISPENSE_INFO_LEN}"
            print(f"  LENGTH MISMATCH: {step.mismatch}")
        print_rows(dispense_info_known_rows(reply.data))
    return step.outcome()


def run_profile_info(pump, steps, cycle):
    """
    Ask about the profile for a cycle. Returns the summary answer.
    """
    reply, step = send(pump, steps, PumpCommand.GET_PROFILE_TRACE_INFO, profile_info_request(cycle),
                       f"GET_PROFILE_TRACE_INFO, cycle {cycle}")
    if not answered(reply):
        return step.outcome()

    info = show_struct(step, ProfileTraceInfo, reply.data)
    if info is None:
        return step.outcome()
    if info.unencoded_size is None:
        return "GOOD, too short"
    if info.unencoded_size == 0:
        return f"cycle {cycle} empty"
    return f"cycle {info.cycle}, {info.unencoded_size} samples, encoded {info.encoded_size}"


def run_alarms(pump, steps):
    """
    Ask for the most recent alarm. Returns the summary answer.
    """
    reply, step = send(pump, steps, PumpCommand.GET_ALARMS, alarm_request(MOST_RECENT_ALARM, 1))
    if not answered(reply):
        return step.outcome()

    try:
        records, leftover = parse_alarms(reply.data)
        for number, record in enumerate(records, 1):
            severity = ALARM_SEVERITY.get(record.alarm_severity, "not a known severity")
            print(f"  record {number}, severity {severity}")
            print_rows(record.rows())
    except Exception as error:
        step.error = f"decode failed, {type(error).__name__}: {error}"
        print(f"  {step.error}")
        return step.outcome()

    if leftover:
        step.mismatch = f"{leftover} bytes left over"
        print(f"  LEFTOVER: {step.mismatch}")

    if not records:
        return "GOOD, no records"
    return f"{len(records)} records, newest alarm {records[0].alarm_number}"


def print_summary(steps, answers):
    good = []
    others = []
    mismatches = []
    for step in steps:
        if step.outcome() == "GOOD":
            good.append(step)
        else:
            others.append(step)
        if step.mismatch is not None:
            mismatches.append(step)

    print("\n\n * SUMMARY\n")
    print(f"  {len(good)} of {len(steps)} GOOD")
    for step in others:
        print(f"  {step.name}: {step.outcome()}")
    for step in mismatches:
        print(f"  {step.name}: {step.mismatch}")
    print()
    for question, answer in answers:
        print(f"  {question + ':':<16} {answer}")


def main():
    print(f"NCS on {HOST}:{PORT}, pump address {PUMP_ADDR} on COM{PUMP_COM}")

    ncs = NCSClient(HOST, PORT)

    # no point sending pump traffic through a box that isn't answering for itself
    print("\n * GET_VERSION\n")
    try:
        print(f"  version: {ncs.get_version()}")
    except NCSError as error:
        print(f"  GET_VERSION -> {type(error).__name__}: {error}")
        print("\n  NCS not answering")
        return 1

    pump = PumpClient(ncs, PUMP_COM, PUMP_ADDR)
    steps = []

    # the commands we expect to work, a failure in this group is a real finding

    reply, step = send(pump, steps, PumpCommand.FIRMWAREINFO)
    if answered(reply):
        show_firmware(step, reply.data)

    profile_cycle = None
    reply, step = send(pump, steps, PumpCommand.CS_RTSTATUS)
    if answered(reply):
        status = show_struct(step, CsRtStatus, reply.data)
        if status is not None and status.profile_cycle is not None:
            profile_cycle = status.profile_cycle

    # carries the same field, so it stands in when CS_RTSTATUS didn't give us a cycle
    reply, step = send(pump, steps, PumpCommand.RTSTATUS)
    if answered(reply):
        status = show_struct(step, RtStatus, reply.data)
        if profile_cycle is None and status is not None and status.profile_cycle is not None:
            profile_cycle = status.profile_cycle

    reply, step = send(pump, steps, PumpCommand.PRESSURE_STATUS)
    if answered(reply):
        show_struct(step, PressureStatus, reply.data)

    reply, step = send(pump, steps, PumpCommand.UTILITY_STATUS)
    if answered(reply):
        if len(reply.data) != UTILITY_STATUS_LEN:
            step.mismatch = f"{len(reply.data)} bytes, expected {UTILITY_STATUS_LEN}"
            print(f"  LENGTH MISMATCH: {step.mismatch}")
        try:
            print_rows(utility_status_known_rows(reply.data))
        except Exception as error:
            step.error = f"decode failed, {type(error).__name__}: {error}"
            print(f"  {step.error}")

    later_structs = [
        (PumpCommand.CYCLECOUNTERS, CycleCounters),
        (PumpCommand.VOLUMECOUNTERS, VolumeCounters),
        (PumpCommand.VERSION, Version),
        (PumpCommand.SERIALNUMBERS, SerialNumbers),
        (PumpCommand.LAST_DIS_INFO_2, LastDispenseInfo2),
    ]
    for cmd, struct_class in later_structs:
        reply, step = send(pump, steps, cmd)
        if answered(reply):
            show_struct(step, struct_class, reply.data)

    # the open questions, any of these may legitimately be refused and the return code is the answer

    svid_answer = run_svids(pump, steps)
    dispense_answer = run_dispense_info(pump, steps)
    send(pump, steps, PumpCommand.DC_STATUS)
    if profile_cycle is None:
        profile_answer = run_profile_info(pump, steps, FALLBACK_PROFILE_CYCLE)
        profile_answer = f"no cycle learned, {profile_answer}"
    else:
        profile_answer = run_profile_info(pump, steps, profile_cycle)
    alarm_answer = run_alarms(pump, steps)

    reply, step = send(pump, steps, PumpCommand.SKIPSTEPINFO)
    if answered(reply):
        show_struct(step, SkipStepInfo, reply.data)

    answers = [
        ("SVIDs", svid_answer),
        ("DISPENSE_INFO", dispense_answer),
        ("Profile", profile_answer),
        ("Alarms", alarm_answer),
    ]
    print_summary(steps, answers)
    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
