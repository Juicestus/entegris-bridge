"""
Every request sent to an NCS box and every reply, kept in memory and written out as files on request, so a run
can be looked at byte for byte afterwards without filling the terminal with hex.
"""

import json
import os
import time
from dataclasses import dataclass

from ncs_protocol import HEADER_LEN, Command
from pump_protocol import FRAME_CMD_OFFSET, PumpCommand

INDEX_FILE_NAME = "index.json"
GATEWAY_RC_LEN = 2      # the return code leads the gateway's reply header


@dataclass
class Exchange:
    seq: int
    sent_at: float          # host clock, unix seconds
    command: str            # the NCS command, and the pump command inside it for SEND_PACKET
    lserial: int
    request: bytes
    response: bytes
    error: str = None       # why no complete reply came back

    def file_stem(self):
        return f"{self.seq:04d}_{self.command}"

    def gateway_rc(self):
        if len(self.response) < GATEWAY_RC_LEN:
            return None
        return int.from_bytes(self.response[:GATEWAY_RC_LEN], "little")


class CommsLog:
    def __init__(self):
        self.exchanges = []

    def record(self, cmd, lserial, request, response, error=None):
        name = Command(cmd).name
        if cmd == Command.SEND_PACKET:
            name += "_" + PumpCommand(request[HEADER_LEN + FRAME_CMD_OFFSET]).name
        self.exchanges.append(Exchange(len(self.exchanges) + 1, time.time(), name, lserial, request, response, error))

    def write(self, directory):
        """
        One file per request and per reply, numbered in the order they were sent, and an index of them all.
        """
        os.makedirs(directory, exist_ok=True)
        index = []
        for exchange in self.exchanges:
            stem = exchange.file_stem()
            with open(os.path.join(directory, stem + "_req.bin"), "wb") as file:
                file.write(exchange.request)
            with open(os.path.join(directory, stem + "_resp.bin"), "wb") as file:
                file.write(exchange.response)
            index.append({
                "seq": exchange.seq,
                "sent_at": exchange.sent_at,
                "command": exchange.command,
                "lserial": exchange.lserial,
                "request_len": len(exchange.request),
                "response_len": len(exchange.response),
                "gateway_rc": exchange.gateway_rc(),
                "error": exchange.error,
            })
        with open(os.path.join(directory, INDEX_FILE_NAME), "w") as file:
            json.dump(index, file, indent=4)
