"""
Unit labels and scale factors for pump values, applied only when printing
"""

from datetime import datetime, timezone

# values stay as the raw integers from the wire everywhere else, so nothing downstream has to guess
# whether a number has been scaled already
CPSI = "cPSI"
MPSI = "mPSI"
PSI = "PSI"
CC = "cC"
UL = "uL"
ML_X1000 = "mL x 1000"
L_X1000 = "L x 1000"
UL_PER_SEC = "uL/sec"
ML_PER_SEC_X1000 = "mL/s x 1000"
MS = "ms"
CS = "cs"
PERCENT = "percent"
EPOCH = "epoch seconds"

# raw value divided by the scale gives the value in the label's unit
SCALES = {
    CPSI: (100, "PSI"),
    MPSI: (1000, "PSI"),
    PSI: (1, "PSI"),
    CC: (100, "C"),
    UL: (1, "uL"),
    ML_X1000: (1000, "mL"),
    L_X1000: (1000, "L"),
    UL_PER_SEC: (1, "uL/s"),
    ML_PER_SEC_X1000: (1000, "mL/s"),
    MS: (1, "ms"),
    CS: (100, "s"),
    PERCENT: (1, "%"),
}


def format_epoch(seconds):
    """
    UTC date and time for an epoch timestamp, with the raw number kept alongside.
    """
    # a misaligned field can hold any number, which may not be a representable date
    try:
        text = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OverflowError, OSError, ValueError):
        return f"{seconds} (not a valid time)"
    return f"{text} ({seconds})"


def format_value(value, unit=None):
    """
    A raw value as text in its unit, showing the raw number too when it was scaled.
    """
    if value is None:
        return "(missing, reply too short)"
    if unit is None:
        return str(value)
    if unit == EPOCH:
        return format_epoch(value)

    scale, label = SCALES[unit]
    if isinstance(value, float):
        return f"{value:g} {label}"
    if scale == 1:
        return f"{value} {label}"

    # the raw number is printed as well, so a wrong scale can be spotted from the output alone
    return f"{value / scale:g} {label} (raw {value})"
