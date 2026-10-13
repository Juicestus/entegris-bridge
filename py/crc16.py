"""
Port of the CRC-16 reference code Entegris supplies for the pump protocol
"""

TABLE_SIZE = 256
BYTE_MASK = 0xFF
WORD_MASK = 0xFFFF
ODD_PARITY_TERM = 0xC001
CRC_LEN = 2

# what the running CRC over a frame plus its own complemented CRC always comes out to, not zero
RESIDUE = 0xB001


def build_table():
    """
    Build the lookup table the same way the reference CRC16_Init does.
    """
    table = []
    for i in range(TABLE_SIZE):
        parity = bin(i).count("1") % 2
        table.append(((i << 7) ^ (i << 6) ^ (parity * ODD_PARITY_TERM)) & WORD_MASK)
    return table


TABLE = build_table()


def crc16(data):
    """
    Running CRC over a byte string, not complemented.
    """
    # the reference feeds each word low byte then high byte, on a little endian buffer that's just the bytes in order
    crc = 0
    for value in data:
        crc = TABLE[(value ^ crc) & BYTE_MASK] ^ (crc >> 8)
    return crc


def append_crc(data):
    """
    Return data with its complemented CRC appended, low byte first.
    """
    check = ~crc16(data) & WORD_MASK
    return data + check.to_bytes(CRC_LEN, "little")


def is_valid(frame):
    """
    Whether a complete frame, CRC included, passes the check.
    """
    return crc16(frame) == RESIDUE
