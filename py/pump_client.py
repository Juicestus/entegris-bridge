import svid
from ncs_protocol import ReturnCode
from pump_protocol import FirmwareInfo, PumpCommand, PumpReturnCode, build_frame, parse_frame
from pump_structs import (
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
    parse_alarms,
    profile_info_request,
    profile_trace_request,
)


class PumpError(Exception):
    """
    Base for everything the pump layer raises, kept apart from NCSError so a caller can tell which layer failed.
    """


class PumpTransportError(PumpError):
    """
    The gateway answered, but the frame did not make it to the pump and back.
    """
    def __init__(self, cmd, gateway_rc):
        super().__init__(f"{PumpCommand(cmd).name} did not reach the pump, gateway return code {int(gateway_rc)}")
        self.cmd = cmd
        self.gateway_rc = gateway_rc


class PumpProtocolError(PumpError):
    """
    Got a reply frame, it does not parse.
    """


class PumpReturnCodeError(PumpError):
    """
    Parsed fine, but the pump answered with a return code other than GOOD.
    """
    def __init__(self, cmd, rc):
        super().__init__(f"{PumpCommand(cmd).name} answered with pump return code {int(rc)}")
        self.cmd = cmd
        self.rc = rc


class PumpClient:
    def __init__(self, ncs, com, addr):
        # the gateway is shared, one box fronts many pumps across its ports
        self.ncs = ncs
        self.com = com
        self.addr = addr

    def command(self, cmd, data=b""):
        """
        Send one command to this pump and return its parsed reply, whatever the pump's return code.
        """
        frame = build_frame(self.addr, cmd, data)
        response = self.ncs.send_packet(self.com, frame)

        # when the pump answers with an error the gateway copies that code into its own header, so a nonzero
        # gateway code can still come with a complete pump reply. only a body that doesn't parse means the frame
        # never made it there and back
        try:
            return parse_frame(response.body, self.addr)
        except ValueError as error:
            if response.rc != ReturnCode.NO_ERR:
                raise PumpTransportError(cmd, response.rc) from error
            raise PumpProtocolError(f"{PumpCommand(cmd).name} reply did not parse: {error}") from error

    def get_firmware_info(self):
        """
        Ask the pump for its firmware information.
        """
        reply = self.command(PumpCommand.FIRMWAREINFO)
        if reply.rc != PumpReturnCode.GOOD:
            raise PumpReturnCodeError(PumpCommand.FIRMWAREINFO, reply.rc)

        try:
            return FirmwareInfo.parse(reply.data)
        except ValueError as error:
            raise PumpProtocolError(f"FIRMWAREINFO reply did not parse: {error}") from error

    def read_data(self, cmd, data=b""):
        """
        Send a command and return the data portion of the reply, raising if the pump refused it.
        """
        reply = self.command(cmd, data)
        if reply.rc != PumpReturnCode.GOOD:
            raise PumpReturnCodeError(cmd, reply.rc)
        return reply.data

    def read_struct(self, cmd, struct_class, data=b""):
        """
        Send a command and decode its reply as a fixed layout struct. Check length_matches on the result.
        """
        return struct_class.parse(self.read_data(cmd, data))

    def get_rt_status(self):
        """
        Ask the pump for its real time status.
        """
        return self.read_struct(PumpCommand.RTSTATUS, RtStatus)

    def get_cs_rt_status(self):
        """
        Ask the pump for its real time status along with its model and serial number.
        """
        return self.read_struct(PumpCommand.CS_RTSTATUS, CsRtStatus)

    def get_pressure_status(self):
        """
        Ask the pump for its dispense and fill pressures.
        """
        return self.read_struct(PumpCommand.PRESSURE_STATUS, PressureStatus)

    def get_cycle_counters(self):
        """
        Ask the pump for its lifetime and resettable cycle counters.
        """
        return self.read_struct(PumpCommand.CYCLECOUNTERS, CycleCounters)

    def get_volume_counters(self):
        """
        Ask the pump for its volume counters.
        """
        return self.read_struct(PumpCommand.VOLUMECOUNTERS, VolumeCounters)

    def get_version(self):
        """
        Ask the pump for its model and version strings.
        """
        return self.read_struct(PumpCommand.VERSION, Version)

    def get_serial_numbers(self):
        """
        Ask the pump for the serial numbers of itself and its parts.
        """
        return self.read_struct(PumpCommand.SERIALNUMBERS, SerialNumbers)

    def get_last_dispense_info(self):
        """
        Ask the pump about its last dispense.
        """
        return self.read_struct(PumpCommand.LAST_DIS_INFO_2, LastDispenseInfo2)

    def get_skip_step_info(self):
        """
        Ask the pump about its last skipped step.
        """
        return self.read_struct(PumpCommand.SKIPSTEPINFO, SkipStepInfo)

    def get_profile_trace_info(self, cycle):
        """
        Ask the pump about the profile recorded for one dispense cycle.
        """
        return self.read_struct(PumpCommand.GET_PROFILE_TRACE_INFO, ProfileTraceInfo, profile_info_request(cycle))

    def get_profile_trace(self, cycle, trace, start, count):
        """
        Ask the pump for count words of one encoded profile trace, starting at word start.
        """
        return self.read_data(PumpCommand.GET_PROFILE_TRACE, profile_trace_request(cycle, trace, start, count))

    def get_alarms(self, start, count):
        """
        Ask the pump for up to count alarm records from start. Returns the records and any bytes left over.
        """
        return parse_alarms(self.read_data(PumpCommand.GET_ALARMS, alarm_request(start, count)))

    def get_svids(self, numbers):
        """
        Ask the pump for a list of SVIDs. Returns the values in order and any bytes left over.
        """
        data = self.read_data(PumpCommand.GET_SVIDS, svid.build_request(numbers))
        try:
            return svid.walk_reply(data, numbers)
        except ValueError as error:
            raise PumpProtocolError(f"GET_SVIDS reply did not parse: {error}") from error
