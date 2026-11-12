import sys
import warnings
from pathlib import Path
import inspect
import importlib.metadata
import socket
import ssl

from cryptography import x509
from cryptography.x509.oid import NameOID

# any outside address works, connecting a UDP socket only picks the route and sends nothing
ROUTE_PROBE = ("8.8.8.8", 80)


def dump_bytes(label, data):
    """
    Hex and ascii dump, same layout as PrintBytes in the C++.
    """
    BYTES_PER_LINE = 16
    PRINTABLE_MIN = 32
    PRINTABLE_MAX = 127

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


def deprecated(exit=True):
    """
    Mark a script as deprecated.
    """
    warnings.simplefilter("always", DeprecationWarning)
    caller = Path(inspect.stack()[1].filename).name

    warnings.warn(
        f"Invoking script {caller}, which has been deprecated.",
        category=DeprecationWarning,
        stacklevel=1
    )
    if exit:
        sys.exit(-1)


def unverified():
    """
    A TLS context that accepts any certificate, so a re-signed one can still be read and reported.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def issuer(der):
    names = x509.load_der_x509_certificate(der).issuer
    found = names.get_attributes_for_oid(NameOID.ORGANIZATION_NAME) or names.get_attributes_for_oid(NameOID.COMMON_NAME)
    return found[0].value if found else names.rfc4514_string()


def local_ip():
    """
    The address this machine sends from, which is what the firewall logs show.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(ROUTE_PROBE)
            return sock.getsockname()[0]
    except OSError:
        return "no route out"


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"
