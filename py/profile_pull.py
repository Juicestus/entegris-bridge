"""
Pull the encoded traces of one profile, a chunk at a time, retrying what the bus damaged and repairing a single
flipped bit as a last resort. Nothing here decodes a trace.
"""

from dataclasses import dataclass, field

from crc_repair import flip, repairing_bits
from ncs_protocol import ReturnCode
from pump_client import PumpError, PumpProtocolError, PumpTransportError
from pump_protocol import PumpReturnCode, parse_frame
from pump_reader import paced
from pump_structs import MAX_TRACE_WORDS, WORD_LEN

# smallest encoded size first, so the simplest traces are known good before the large pressure traces, which lean
# hardest on the delta coding, are pulled
TRACE_ORDER = [7, 9, 8, 5, 6, 2, 3, 4, 1, 0]

# the bus flips the odd bit, so a damaged or lost reply is asked for again. a request the gateway itself rejected
# as a bad CRC is our own bug and isn't, sending it again would only fail the same way
RETRY_GATEWAY_CODES = [ReturnCode.SERIAL_OUTPUT_INVALID_CRC, ReturnCode.SERIAL_READ_TIMEOUT]
MAX_RETRIES = 3


@dataclass
class Repair:
    attempt: int        # which failed reply was repaired
    byte: int           # offset in the pump frame
    bit: int


@dataclass
class ChunkRecord:
    start: int
    count: int
    attempts: int = 0
    failed: bool = False
    errors: list = field(default_factory=list)      # one line per failed attempt
    bad_replies: list = field(default_factory=list)     # (attempt, raw frame) for every reply that failed its checks
    repair: Repair = None


@dataclass
class TracePull:
    trace: int
    expected_words: int
    data: bytes = b""
    chunks: list = field(default_factory=list)

    def complete(self):
        return len(self.data) == self.expected_words * WORD_LEN

    def repaired(self):
        return any(chunk.repair is not None for chunk in self.chunks)


def should_retry(error):
    return isinstance(error, PumpTransportError) and error.gateway_rc in RETRY_GATEWAY_CODES


def try_repair(pump, record):
    """
    The chunk's data from a failed reply that one flipped bit makes whole, or None. Only trusted when exactly one
    bit repairs it and the result is a good reply of the size asked for, anything else is refused.
    """
    for attempt, frame in record.bad_replies:
        found = repairing_bits(frame)
        if len(found) != 1:
            continue
        offset, bit = found[0]
        try:
            reply = parse_frame(flip(frame, offset, bit), pump.addr)
        except ValueError:
            continue
        if reply.rc != PumpReturnCode.GOOD or len(reply.data) != record.count * WORD_LEN:
            continue
        record.repair = Repair(attempt, offset, bit)
        return reply.data
    return None


def request_chunk(pump, cycle, trace, record):
    """
    One chunk of a trace, retried when the bus lost or damaged it and repaired if every attempt failed. Returns the
    bytes, or None with the record saying why.
    """
    while True:
        record.attempts += 1
        try:
            return paced(pump.get_profile_trace, cycle, trace, record.start, record.count)
        except PumpError as error:
            record.errors.append(f"attempt {record.attempts}: {type(error).__name__}: {error}")
            if isinstance(error, (PumpTransportError, PumpProtocolError)) and error.body:
                record.bad_replies.append((record.attempts, error.body))
            if record.attempts > MAX_RETRIES or not should_retry(error):
                break

    data = try_repair(pump, record)
    if data is None:
        record.failed = True
    return data


def pull_trace(pump, cycle, trace, expected_words):
    """
    All the words of one trace. Stops at the first chunk that can't be had, so the words kept are always in order.
    """
    pull = TracePull(trace, expected_words)
    start = 0
    while start < expected_words:
        record = ChunkRecord(start, min(MAX_TRACE_WORDS, expected_words - start))
        pull.chunks.append(record)
        chunk = request_chunk(pump, cycle, trace, record)
        if chunk is None:
            break

        received_words = len(chunk) // WORD_LEN
        pull.data += chunk
        # an empty answer would otherwise ask for the same words forever
        if received_words == 0:
            record.failed = True
            record.errors.append("empty reply")
            break
        start += received_words
    return pull


def pull_profile(pump, cycle, info):
    """
    Every trace of one profile, in trace number order.
    """
    pulls = {}
    for trace in TRACE_ORDER:
        pulls[trace] = pull_trace(pump, cycle, trace, info.encoded_size[trace])
    return [pulls[trace] for trace in sorted(pulls)]
