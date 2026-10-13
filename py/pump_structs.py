"""
Fixed layout pump replies. Every layout here was transcribed by hand and is unconfirmed until checked against
real bytes, so parsing records a length mismatch instead of raising.
"""

import struct
from dataclasses import dataclass, field, fields
from typing import ClassVar

from units import CC, CPSI, EPOCH, L_X1000, ML_X1000, MPSI, PERCENT, PSI, UL

ENDIAN = "<"
STRING_CODE = "s"


def spec(code, label, unit=None):
    """
    A field with its struct code, its name in the spec, and its unit.
    """
    return field(default=None, metadata={"code": code, "label": label, "unit": unit, "reserved": False})


def reserved(code, label):
    """
    A reserved field, kept so the offsets after it stay right but never printed.
    """
    return field(default=None, metadata={"code": code, "label": label, "unit": None, "reserved": True})


def decode_string(raw):
    """
    Text out of a fixed width null padded field.
    """
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def unpack_field(code, data, offset):
    """
    The value of one field at offset and its size, or None for the value if the data runs out first.
    """
    size = struct.calcsize(ENDIAN + code)
    if offset + size > len(data):
        return None, size

    unpacked = struct.unpack_from(ENDIAN + code, data, offset)
    if code.endswith(STRING_CODE):
        return decode_string(unpacked[0]), size
    if len(unpacked) == 1:
        return unpacked[0], size
    return list(unpacked), size


@dataclass
class PumpStruct:
    """
    Base for a fixed layout reply, parsed field by field so a short reply still decodes what it has.
    """
    received_len: int = None

    EXPECTED_LEN: ClassVar[int] = 0     # the size the spec states, checked against the layout below

    @classmethod
    def layout(cls):
        """
        The wire fields in order, reserved ones included.
        """
        return [f for f in fields(cls) if "code" in f.metadata]

    @classmethod
    def format(cls):
        """
        The whole layout as one struct format string.
        """
        codes = ""
        for f in cls.layout():
            codes += f.metadata["code"]
        return ENDIAN + codes

    @classmethod
    def length(cls):
        """
        Size in bytes of the whole layout.
        """
        return struct.calcsize(cls.format())

    @classmethod
    def parse(cls, data):
        """
        Decode the data portion of a reply. Fields past the end of a short reply stay None.
        """
        values = {}
        offset = 0
        for f in cls.layout():
            value, size = unpack_field(f.metadata["code"], data, offset)
            values[f.name] = value
            offset += size
        return cls(received_len=len(data), **values)

    def length_matches(self):
        """
        Whether the reply was exactly the size of the layout.
        """
        return self.received_len == self.length()

    def rows(self):
        """
        Label, value and unit for every field that isn't reserved.
        """
        result = []
        for f in self.layout():
            if not f.metadata["reserved"]:
                result.append((f.metadata["label"], getattr(self, f.name), f.metadata["unit"]))
        return result


@dataclass
class VolumeCounters(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 24

    start_cycle: int = spec("L", "StartCycle")
    dispensed: int = spec("L", "Dispensed", UL)
    soft_trigger: int = spec("L", "SoftTrigger", UL)
    purged: int = spec("L", "Purged", UL)
    vented: int = spec("L", "Vented", UL)
    reserved1: int = reserved("L", "Reserved1")


@dataclass
class CycleCounters(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 112

    cycle_record_num: int = spec("L", "CycleRecordNum")
    cycle_counts: int = spec("L", "CycleCounts")
    power_cycles: int = spec("L", "PowerCycles")
    dispense_volume: int = spec("L", "DispenseVolume", L_X1000)
    filtrated_volume: int = spec("L", "FiltratedVolume", L_X1000)
    vented_volume: int = spec("L", "VentedVolume", L_X1000)
    purged_volume: int = spec("L", "PurgedVolume", L_X1000)
    filled_volume: int = spec("L", "FilledVolume", L_X1000)
    cycle_counts_reset_a: int = spec("L", "CycleCounts_reset_A")
    power_cycles_reset_a: int = spec("L", "PowerCycles_reset_A")
    dispense_volume_reset_a: int = spec("L", "DispenseVolume_reset_A", L_X1000)
    filtrated_volume_reset_a: int = spec("L", "FiltratedVolume_reset_A", L_X1000)
    vented_volume_reset_a: int = spec("L", "VentedVolume_reset_A", L_X1000)
    purged_volume_reset_a: int = spec("L", "PurgedVolume_reset_A", L_X1000)
    filled_volume_reset_a: int = spec("L", "FilledVolume_reset_A", L_X1000)
    cycle_counts_reset_b: int = spec("L", "CycleCounts_reset_B")
    power_cycles_reset_b: int = spec("L", "PowerCycles_reset_B")
    dispense_volume_reset_b: int = spec("L", "DispenseVolume_reset_B", L_X1000)
    filtrated_volume_reset_b: int = spec("L", "FiltratedVolume_reset_B", L_X1000)
    vented_volume_reset_b: int = spec("L", "VentedVolume_reset_B", L_X1000)
    purged_volume_reset_b: int = spec("L", "PurgedVolume_reset_B", L_X1000)
    filled_volume_reset_b: int = spec("L", "FilledVolume_reset_B", L_X1000)
    reserved1: int = reserved("L", "Reserved1")
    reserved2: int = reserved("L", "Reserved2")
    reserved3: int = reserved("L", "Reserved3")
    reserved4: int = reserved("L", "Reserved4")
    reserved5: int = reserved("L", "Reserved5")
    reserved10: int = reserved("H", "Reserved10")
    crc: int = spec("H", "CRC")


@dataclass
class RtStatusCommon(PumpStruct):
    """
    The part RTSTATUS and CS_RTSTATUS share, they are identical up to and including OfflineState.
    """
    status: int = spec("h", "Status")
    indicators: int = spec("H", "Indicators")
    last_dispense_cycle: int = spec("L", "LastDispenseCycle")
    last_recipe_num: int = spec("H", "LastRecipeNum")
    last_dispense_vol_ul: int = spec("H", "LastDispenseVol_uL", UL)
    last_dispense_confirmation_per: int = spec("H", "LastDispenseConfirmation_per", PERCENT)
    reserved1: int = reserved("H", "reserved1")
    total_cycle_counts: int = spec("L", "TotalCycleCounts")
    resettable_cycle_counts: int = spec("L", "ResettableCycleCounts")
    resettable_filtrated_volume: int = spec("L", "ResettableFiltratedVolume", L_X1000)
    critical_errors: int = spec("H", "CriticalErrors")
    errors: int = spec("H", "Errors")
    warnings: int = spec("H", "Warnings")
    infos: int = spec("H", "Infos")
    total_alarms: int = spec("H", "TotalAlarms")
    reserved2: int = reserved("H", "reserved2")
    newest_alarm_num: int = spec("L", "NewestAlarmNum")
    newest_record_num: int = spec("L", "NewestRecordNum")
    last_cleared_record: int = spec("L", "LastClearedRecord")
    last_cleared_alarm: int = spec("L", "LastClearedAlarm")
    profile_cycle: int = spec("L", "ProfileCycle")
    n2_sensor_cpsi: int = spec("h", "N2Sensor_cPSI", CPSI)
    vac_sensor_cpsi: int = spec("h", "VacSensor_cPSI", CPSI)
    offline_state: int = spec("H", "OfflineState")


@dataclass
class RtStatus(RtStatusCommon):
    EXPECTED_LEN: ClassVar[int] = 76

    external_rec_valve_enable: int = spec("H", "ExternalRecValveEnable")
    consecutive_auto_vents: int = spec("l", "ConsecutiveAutoVents")
    reserved5: int = reserved("l", "reserved5")


@dataclass
class CsRtStatus(RtStatusCommon):
    # carries the model and serial number, which is what lets a fleet be keyed on the pump rather than the cable
    EXPECTED_LEN: ClassVar[int] = 124

    model: str = spec("24s", "Model")
    pump_serial_number: str = spec("24s", "PumpSerialNumber")
    reserved3: int = reserved("H", "reserved3")
    reserved4: int = reserved("l", "reserved4")
    reserved5: int = reserved("l", "reserved5")


@dataclass
class PressureStatus(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 68

    reserved1: int = reserved("h", "reserved1")
    dis_uncal_adc: int = spec("H", "Dis_UnCal_ADC")
    dis_uncal_psi: float = spec("f", "Dis_UnCal_PSI", PSI)
    dis_uncal_mpsi: int = spec("l", "Dis_UnCal_mPSI", MPSI)
    dis_pressure_max_range_mpsi: int = spec("l", "Dis_pressure_Max_Range_mPSI", MPSI)
    dis_pressure_min_range_mpsi: int = spec("l", "Dis_pressure_Min_Range_mPSI", MPSI)
    dis_pressure_psi: float = spec("f", "Dis_pressure_PSI", PSI)
    dis_pressure_mpsi: int = spec("l", "Dis_pressure_mPSI", MPSI)
    dis_pressure_cpsi: int = spec("h", "Dis_pressure_cPSI", CPSI)
    fill_uncal_adc: int = spec("H", "Fill_UnCal_ADC")
    fill_uncal_psi: float = spec("f", "Fill_UnCal_PSI", PSI)
    fill_uncal_mpsi: int = spec("l", "Fill_UnCal_mPSI", MPSI)
    fill_pressure_max_range_mpsi: int = spec("l", "Fill_pressure_Max_Range_mPSI", MPSI)
    fill_pressure_min_range_mpsi: int = spec("l", "Fill_pressure_Min_Range_mPSI", MPSI)
    fill_pressure_psi: float = spec("f", "Fill_pressure_PSI", PSI)
    fill_pressure_mpsi: int = spec("l", "Fill_pressure_mPSI", MPSI)
    fill_pressure_cpsi: int = spec("h", "Fill_pressure_cPSI", CPSI)
    reserved2: int = reserved("h", "reserved2")
    dis_pressure_sensor_errors: int = spec("L", "disPressureSensor_ERRORS")
    fill_pressure_sensor_errors: int = spec("L", "fillPressureSensor_ERRORS")


@dataclass
class Version(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 104

    reserved1: str = reserved("16s", "reserved1")
    reserved2: str = reserved("16s", "reserved2")
    model: str = spec("24s", "Model")
    version: str = spec("48s", "Version")


@dataclass
class SerialNumbers(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 296

    pump: str = spec("24s", "Pump")
    fill_motor: str = spec("24s", "FillMotor")
    dispense_motor: str = spec("24s", "DispenseMotor")
    fill_pressure: str = spec("24s", "FillPressure")
    dispense_pressure: str = spec("24s", "DispensePressure")
    main_board: str = spec("24s", "MainBoard")
    internal_board: str = spec("24s", "InternalBoard")
    interface_board: str = spec("24s", "InterfaceBoard")
    reserved1: str = reserved("24s", "Reserved1")
    reserved2: str = reserved("24s", "Reserved2")
    reserved3: str = reserved("24s", "Reserved3")
    reserved4: str = reserved("24s", "Reserved4")
    time: int = spec("L", "Time", EPOCH)
    cycle: int = spec("L", "Cycle")


@dataclass
class LastDispenseInfo2(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 16

    cycle: int = spec("L", "Cycle")
    dispense_volume: int = spec("L", "DispenseVolume", ML_X1000)
    reserved1: int = reserved("L", "reserved1")
    reserved2: int = reserved("L", "reserved2")


@dataclass
class SkipStepInfo(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 16

    cycle: int = spec("L", "Cycle")
    step_mismatch_ul: int = spec("h", "StepMismatch_uL", UL)
    reserved1: int = reserved("h", "reserved1")
    reserved2: int = reserved("L", "reserved2")
    reserved3: int = reserved("L", "reserved3")


@dataclass
class ProfileTraceInfo(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 76

    cycle: int = spec("L", "Cycle")
    type_encoding: int = spec("H", "Type_Encoding")     # low byte is the encoding, high byte the profile type
    sample_interval: int = spec("H", "SampleInterval")
    time: int = spec("L", "Time", EPOCH)
    start_cycle: int = spec("L", "StartCycle")
    dispense_end: int = spec("L", "DispenseEnd")
    end_filtration: int = spec("L", "EndFiltration")
    end_cycle: int = spec("L", "EndCycle")
    fill_end: int = spec("L", "FillEnd")
    unencoded_size: int = spec("L", "UnencodedSize")
    encoded_size: list = spec("10L", "EncodedSize")     # one per trace


@dataclass
class AlarmRecord(PumpStruct):
    EXPECTED_LEN: ClassVar[int] = 28

    size: int = spec("H", "Size")       # in words
    record_type: int = spec("H", "RecordType")
    cycle_num: int = spec("L", "CycleNum")
    time_stamp: int = spec("L", "TimeStamp", EPOCH)
    record_number: int = spec("L", "RecordNumber")
    alarm_number: int = spec("L", "AlarmNumber")
    code: int = spec("H", "Code")
    alarm_severity: int = spec("H", "AlarmSeverity")
    data: int = spec("L", "Data")       # meaning depends on the alarm


FIXED_STRUCTS = [
    VolumeCounters,
    CycleCounters,
    RtStatus,
    CsRtStatus,
    PressureStatus,
    Version,
    SerialNumbers,
    LastDispenseInfo2,
    SkipStepInfo,
    ProfileTraceInfo,
    AlarmRecord,
]

# catch a transcription typo in a field type as soon as this is imported
for struct_class in FIXED_STRUCTS:
    assert struct_class.length() == struct_class.EXPECTED_LEN, struct_class.__name__

ALARM_SEVERITY = {0: "none", 1: "info", 2: "warning", 3: "error", 4: "critical"}

PROFILE_INFO_REQUEST_FORMAT = ENDIAN + "L"

# alarm number, count, reserved
ALARM_REQUEST_FORMAT = ENDIAN + "LHH"
MOST_RECENT_ALARM = 0
MIN_ALARM_COUNT = 1
MAX_ALARM_COUNT = 25


def profile_info_request(cycle):
    """
    Send data for GET_PROFILE_TRACE_INFO.
    """
    return struct.pack(PROFILE_INFO_REQUEST_FORMAT, cycle)


def alarm_request(start, count):
    """
    Send data for GET_ALARMS, a start of MOST_RECENT_ALARM means the newest.
    """
    if not MIN_ALARM_COUNT <= count <= MAX_ALARM_COUNT:
        raise ValueError(f"alarm count {count} is outside {MIN_ALARM_COUNT} to {MAX_ALARM_COUNT}")
    return struct.pack(ALARM_REQUEST_FORMAT, start, count, 0)


def parse_alarms(data):
    """
    Split a GET_ALARMS reply into records, and return them with the number of bytes left over.
    """
    # the pump stops searching after a time limit, so there can be fewer records than were asked for
    records = []
    record_len = AlarmRecord.length()
    offset = 0
    while offset + record_len <= len(data):
        records.append(AlarmRecord.parse(data[offset:offset + record_len]))
        offset += record_len
    return records, len(data) - offset


# only the first fields of UTILITY_STATUS are known. VacSensorTemp_cC and several motor currents in mA come
# somewhere after them, but their offsets aren't confirmed, so everything past these stays raw for now
UTILITY_STATUS_LEN = 44
UTILITY_STATUS_KNOWN = [
    ("TempFillSensor_cC", "h", CC),
    ("TempDisSensor_cC", "h", CC),
    ("N2SensorTemp_cC", "h", CC),
]


def utility_status_known_rows(data):
    """
    Label, value and unit for the fields at the start of UTILITY_STATUS that we know.
    """
    rows = []
    offset = 0
    for label, code, unit in UTILITY_STATUS_KNOWN:
        value, size = unpack_field(code, data, offset)
        rows.append((label, value, unit))
        offset += size
    return rows


# DISPENSE_INFO needs ENGINEER access and is only partly known, so it is printed raw. these are the fields at
# its start that we do know, as offset, type and name, kept for writing its dataclass once real bytes are in.
# DC_STATUS has no known layout or size at all.
DISPENSE_INFO_KNOWN = [
    (0, "UINT16", "RecipeNum"),
    (4, "ULONG", "Cycle"),
    (8, "ULONG", "ReferenceCycle"),
    (12, "ULONG", "Time"),
    (16, "STRING[24]", "JobInfo"),
    (40, "UINT16", "SlotID"),
    (44, "INT16", "ReadyPressure"),
]
