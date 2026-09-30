BYTES_PER_LINE = 16
PRINTABLE_MIN = 32
PRINTABLE_MAX = 127


def dump_bytes(label, data):
    """
    Hex and ascii dump, same layout as PrintBytes in the C++.
    """
    print(f"{label} {len(data)} bytes")

    for offset in range(0, len(data), BYTES_PER_LINE):
        chunk = data[offset:offset + BYTES_PER_LINE]

        hex_part = ""
        ascii_part = ""
        for value in chunk:
            hex_part += f" {value:02x}"
            if PRINTABLE_MIN <= value < PRINTABLE_MAX:
                ascii_part += chr(value)
            else:
                ascii_part += "."

        # pad the last short line so the ascii column stays put
        padding = "   " * (BYTES_PER_LINE - len(chunk))
        print(f"    {offset:04x} {hex_part}{padding}  |{ascii_part}|")
