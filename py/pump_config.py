"""
The pumps to collect from, set by the fab's engineers. A CSV for now, a table in the app later.

    address,frequency_s,name
    2:10.25.85.52:8888:COM8,300,Cool pump
    1:10.25.85.52:8888:COM2,100,
"""

import csv
from dataclasses import dataclass

from ncs_protocol import COM_PORT_COUNT
from pump_protocol import MAX_ADDR, MIN_ADDR

SEPARATOR = ":"
COM_PREFIX = "COM"
FIELD_COUNT = 4
MIN_COM = 1
MIN_PORT = 1
MAX_PORT = 0xFFFF


@dataclass(frozen=True)
class PumpAddress:
    """
    The NCS box a pump sits behind, the COM port on that box, and the pump's address on that port's bus.
    """
    addr: int
    host: str
    port: int
    com: int

    def __post_init__(self):
        if not MIN_ADDR <= self.addr <= MAX_ADDR:
            raise ValueError(f"pump address {self.addr} is outside {MIN_ADDR} to {MAX_ADDR}")
        if not self.host:
            raise ValueError("NCS host is empty")
        if not MIN_PORT <= self.port <= MAX_PORT:
            raise ValueError(f"NCS port {self.port} is outside {MIN_PORT} to {MAX_PORT}")
        if not MIN_COM <= self.com <= COM_PORT_COUNT:
            raise ValueError(f"{COM_PREFIX}{self.com} is outside {COM_PREFIX}{MIN_COM} to {COM_PREFIX}{COM_PORT_COUNT}")

    @classmethod
    def parse(cls, text):
        """
        Read an address written the way the vendor's MMI shows it, such as 2:10.25.85.52:8888:COM8.
        """
        parts = text.strip().split(SEPARATOR)
        if len(parts) != FIELD_COUNT or not parts[3].upper().startswith(COM_PREFIX):
            raise ValueError(f"{text!r} is not written as addr:host:port:COMn")

        try:
            addr = int(parts[0])
            port = int(parts[2])
            com = int(parts[3][len(COM_PREFIX):])
        except ValueError as error:
            raise ValueError(f"{text!r} has a field that isn't a number") from error
        return cls(addr, parts[1], port, com)

    def __str__(self):
        return SEPARATOR.join([str(self.addr), self.host, str(self.port), f"{COM_PREFIX}{self.com}"])


ADDRESS_COLUMN = "address"
FREQUENCY_COLUMN = "frequency_s"
NAME_COLUMN = "name"


@dataclass(frozen=True)
class PumpConfig:
    address: PumpAddress
    frequency_s: int            # how often to collect, read but unused until there's a scheduler
    name: str = None            # a local alias for logs, the pump reports its own name

    def label(self):
        if self.name:
            return f"{self.name} ({self.address})"
        return str(self.address)


def load_config(path):
    """
    Every pump in the file, in file order. Raises on the first row that doesn't parse, naming its line.
    """
    configs = []
    with open(path, newline="") as file:
        reader = csv.DictReader(file)
        missing = {ADDRESS_COLUMN, FREQUENCY_COLUMN} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} has no {', '.join(sorted(missing))} column")

        for row in reader:
            if not (row[ADDRESS_COLUMN] or "").strip():
                continue
            try:
                configs.append(PumpConfig(
                    address=PumpAddress.parse(row[ADDRESS_COLUMN]),
                    frequency_s=int(row[FREQUENCY_COLUMN]),
                    name=(row.get(NAME_COLUMN) or "").strip() or None,
                ))
            except ValueError as error:
                raise ValueError(f"{path} line {reader.line_num}: {error}") from error

    addresses = [config.address for config in configs]
    if len(set(addresses)) != len(addresses):
        raise ValueError(f"{path} lists the same pump more than once")
    return configs
