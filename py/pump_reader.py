"""
Read a pump into plain dataclasses in our own terms. Knows the commands, not the database.
"""

import time
from dataclasses import dataclass, field

from ncs_protocol import ReturnCode
from pump_client import PumpError, PumpReturnCodeError, PumpTransportError
from pump_protocol import PumpCommand, PumpReturnCode
from pump_structs import (
    DISPENSE_INFO_LEN,
    MOST_RECENT_DISPENSE,
    CsRtStatus,
    LastDispenseInfo2,
    ProfileTraceInfo,
    RtStatus,
    SerialNumbers,
    Version,
    dispense_info_known_rows,
    dispense_info_request,
    profile_info_request,
)

# the pump answers one command at a time on a shared bus, so commands are spaced out
COMMAND_GAP_SEC = 0.2
# after a read timeout the pump can still be finishing the lost reply, so the next command waits it out
TIMEOUT_RECOVERY_SEC = 10

# read together in one request, falling back to one at a time if the pump doesn't know one of them
NAME_SVIDS = {
    "firmware_string": 11,
    "customer_name": 12,
    "fab_name": 13,
    "tool_name": 14,
    "coater_name": 15,
    "nozzle_name": 16,
    "resist_name": 17,
    "pump_name": 18,
    "fluid_name": 19,
    "pump_time": 1025,
}
# these describe the most recent dispense only, like DISPENSE_INFO itself
DISPENSE_SVIDS = {
    "recipe_title": 20,
    "confirmation_pct": 849,
    "confirmation_time": 1071,
}


def paced(read, *args):
    """
    Run one pump read after the bus gap, waiting out a read timeout before passing its error on.
    """
    time.sleep(COMMAND_GAP_SEC)
    try:
        return read(*args)
    except PumpTransportError as error:
        if error.gateway_rc == ReturnCode.SERIAL_READ_TIMEOUT:
            time.sleep(TIMEOUT_RECOVERY_SEC)
        raise


@dataclass
class PumpInfo:
    serial: str                         # SERIALNUMBERS, the one outputs are filed under
    serial_from_status: str = None      # CS_RTSTATUS, a second source that should agree
    model: str = None
    firmware: str = None
    module_type: int = None
    names: dict = field(default_factory=dict)     # every NAME_SVIDS entry, None where the pump didn't give one
    read_at: float = None               # host clock, unix seconds
    problems: list = field(default_factory=list)  # reads that failed, kept so a gap can be explained

    def clock_offset_sec(self):
        """
        How far the pump's clock is ahead of the host's, or None if the pump time wasn't read.
        """
        pump_time = self.names.get("pump_time")
        if pump_time is None:
            return None
        return pump_time - self.read_at


@dataclass
class DispenseHeader:
    """
    What the pump says about its most recent dispense. Can't be asked for any other cycle.
    """
    cycle: int = None
    recipe_number: int = None
    reference_cycle: int = None
    time: int = None
    job_info: str = None
    slot_id: int = None
    ready_pressure_cpsi: int = None
    reference_ready_pressure_cpsi: int = None
    recipe_title: str = None
    confirmation_pct: int = None
    volume_setpoint_ul: int = None      # RTSTATUS, appears to be the volume asked for
    volume_measured_ml_x1000: int = None    # LAST_DIS_INFO_2, appears to be the volume measured
    read_at: float = None
    problems: list = field(default_factory=list)


def read_svids(pump, wanted, problems):
    """
    Values for a {name: SVID number} map, None for any the pump didn't give.
    """
    numbers = list(wanted.values())
    try:
        values, leftover = paced(pump.get_svids, numbers)
        return {name: value.value for name, value in zip(wanted, values)}
    except PumpReturnCodeError as error:
        if error.rc != PumpReturnCode.UNKNOWN_SVID:
            raise
        problems.append(f"GET_SVIDS {numbers}: {error}, read one at a time")

    result = {}
    for name, number in wanted.items():
        try:
            values, leftover = paced(pump.get_svids, [number])
            result[name] = values[0].value
        except PumpError as error:
            problems.append(f"SVID {number}: {error}")
            result[name] = None
    return result


def read_struct(pump, cmd, struct_class, problems):
    """
    One fixed layout reply, or None with the reason kept.
    """
    try:
        return paced(pump.read_struct, cmd, struct_class)
    except PumpError as error:
        problems.append(f"{cmd.name}: {error}")
        return None


def read_pump_info(pump):
    """
    Who the pump is. Raises if no serial number can be read, since nothing can be filed without one.
    """
    problems = []
    read_at = time.time()
    serials = read_struct(pump, PumpCommand.SERIALNUMBERS, SerialNumbers, problems)
    status = read_struct(pump, PumpCommand.CS_RTSTATUS, CsRtStatus, problems)

    # checked before anything else is asked, a pump that isn't there costs a timeout per command
    serial = serials.pump if serials is not None else None
    serial_from_status = status.pump_serial_number if status is not None else None
    if not serial and not serial_from_status:
        raise PumpError(f"no serial number: {'; '.join(problems)}")
    if serial and serial_from_status and serial != serial_from_status:
        problems.append(f"serial numbers disagree: SERIALNUMBERS {serial}, CS_RTSTATUS {serial_from_status}")

    version = read_struct(pump, PumpCommand.VERSION, Version, problems)

    module_type = None
    try:
        module_type = paced(pump.get_firmware_info).module_type
    except PumpError as error:
        problems.append(f"FIRMWAREINFO: {error}")

    try:
        names = read_svids(pump, NAME_SVIDS, problems)
    except PumpError as error:
        problems.append(f"GET_SVIDS: {error}")
        names = {name: None for name in NAME_SVIDS}

    return PumpInfo(
        serial=serial or serial_from_status,
        serial_from_status=serial_from_status,
        model=version.model if version is not None else (status.model if status is not None else None),
        firmware=version.version if version is not None else names.get("firmware_string"),
        module_type=module_type,
        names=names,
        read_at=read_at,
        problems=problems,
    )


def read_newest_cycle(pump):
    """
    The pump's newest profile cycle, from CS_RTSTATUS or RTSTATUS if that fails. None if neither gives one.
    """
    problems = []
    for cmd, struct_class in [(PumpCommand.CS_RTSTATUS, CsRtStatus), (PumpCommand.RTSTATUS, RtStatus)]:
        status = read_struct(pump, cmd, struct_class, problems)
        if status is not None and status.profile_cycle is not None:
            return status.profile_cycle
    return None


def read_profile_info(pump, cycle):
    """
    The profile info for one cycle and its raw bytes, or None for both if the pump doesn't hold that cycle.
    """
    reply = paced(pump.command, PumpCommand.GET_PROFILE_TRACE_INFO, profile_info_request(cycle))
    # a refusal carries stale bytes that look like real profile info, so nothing is parsed unless it's GOOD
    if reply.rc == PumpReturnCode.PROFILE_NOT_HELD:
        return None, None
    if reply.rc != PumpReturnCode.GOOD:
        raise PumpReturnCodeError(PumpCommand.GET_PROFILE_TRACE_INFO, reply.rc)
    return ProfileTraceInfo.parse(reply.data), reply.data


def read_dispense_cycle(pump):
    """
    The cycle DISPENSE_INFO is describing right now, used to check nothing dispensed while a profile was pulled.
    """
    data = paced(pump.read_data, PumpCommand.DISPENSE_INFO, dispense_info_request(MOST_RECENT_DISPENSE))
    return dict((label, value) for label, value, unit in dispense_info_known_rows(data))["Cycle"]


def read_dispense_header(pump):
    """
    Everything known about the most recent dispense. Each part that can't be tied to that same cycle is left out.
    """
    header = DispenseHeader(read_at=time.time())
    try:
        data = paced(pump.read_data, PumpCommand.DISPENSE_INFO, dispense_info_request(MOST_RECENT_DISPENSE))
    except PumpError as error:
        header.problems.append(f"DISPENSE_INFO: {error}")
        return header
    if len(data) != DISPENSE_INFO_LEN:
        header.problems.append(f"DISPENSE_INFO is {len(data)} bytes, expected {DISPENSE_INFO_LEN}")

    known = dict((label, value) for label, value, unit in dispense_info_known_rows(data))
    header.cycle = known["Cycle"]
    header.recipe_number = known["RecipeNum"]
    header.reference_cycle = known["ReferenceCycle"]
    header.time = known["Time"]
    header.job_info = known["JobInfo"]
    header.slot_id = known["SlotID"]
    header.ready_pressure_cpsi = known["ReadyPressure"]
    header.reference_ready_pressure_cpsi = known["ReferenceReadyPressure"]

    # the other sources each say which dispense they describe, and are only used when it's this one
    last = read_struct(pump, PumpCommand.LAST_DIS_INFO_2, LastDispenseInfo2, header.problems)
    if last is not None and last.cycle == header.cycle:
        header.volume_measured_ml_x1000 = last.dispense_volume
    elif last is not None:
        header.problems.append(f"LAST_DIS_INFO_2 is for cycle {last.cycle}, not {header.cycle}")

    status = read_struct(pump, PumpCommand.RTSTATUS, RtStatus, header.problems)
    if status is not None and status.last_dispense_cycle == header.cycle:
        header.volume_setpoint_ul = status.last_dispense_vol_ul
    elif status is not None:
        header.problems.append(f"RTSTATUS last dispense is cycle {status.last_dispense_cycle}, not {header.cycle}")

    try:
        values = read_svids(pump, DISPENSE_SVIDS, header.problems)
    except PumpError as error:
        header.problems.append(f"GET_SVIDS: {error}")
        values = {}
    header.recipe_title = values.get("recipe_title")
    if values.get("confirmation_time") == header.time:
        header.confirmation_pct = values.get("confirmation_pct")
    elif values:
        header.problems.append("confirmation SVIDs are for a different dispense time")
    return header
