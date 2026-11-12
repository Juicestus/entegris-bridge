"""
Check the Azure database and blob container in an env file can be reached, one network layer at a time and then
the way the bridge writes, without leaving anything behind. Every check runs even after one fails, and each failure
carries a hint saying what to ask the network admins for.

    py azure_check.py [cred.env]
"""

import argparse
import email.utils
import os
import platform
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pandas as pd
import pg8000.dbapi
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.storage.blob import ContainerClient

from azure_writer import OBJECT_EXTENSION, AzureBlobStorageWriter, AzureConfig, create_azure_writer
from postgres_writer import TEST_PATH, check_database
from util import issuer, local_ip, package_version, unverified

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(HERE, "cred.env")

HTTPS_PORT = 443
TIMEOUT_SEC = 10
MAX_CLOCK_SKEW_SEC = 60
# Azure's certificates come from Microsoft's own authorities, or from DigiCert for older servers
AZURE_ISSUERS = ["Microsoft", "DigiCert"]
PACKAGES = ["azure-storage-blob", "pg8000", "python-dotenv", "cryptography"]
# Postgres starts in plain text and only switches to TLS once asked, with this message, then answers S for yes
POSTGRES_SSL_REQUEST = struct.pack("!ii", 8, 80877103)
POSTGRES_SSL_YES = b"S"
# what Postgres answers for a wrong password, and for a login from somewhere the server doesn't allow
PG_BAD_PASSWORD = "28P01"
PG_NOT_ALLOWED = "28000"
# what Azure Storage answers when its own firewall turns the request away
STORAGE_NOT_ALLOWED = "AuthorizationFailure"
# what kind of problem a hint points at, so whoever reads it knows who to take it to
NETWORK = "network"
SETTINGS = "settings"
MACHINE = "machine"


class Failure(Exception):
    def __init__(self, detail, hint=None):
        super().__init__(detail)
        self.hint = hint


def first_line(error):
    lines = str(error).splitlines()
    return lines[0] if lines else ""


class Report:
    def __init__(self):
        self.hints = []
        self.failed = False

    def step(self, name, check, *args):
        """
        Run one check and print how it went. Returns what the check returned, or None if it failed.
        """
        try:
            result = check(*args)
        except Failure as failure:
            self.fail(name, str(failure), failure.hint)
            return None
        except Exception as error:
            # some errors run on for lines, the first one says what went wrong
            self.fail(name, f"{type(error).__name__}: {first_line(error)}", None)
            return None
        print(f"  {'OK':6}  {name:24}  {result}")
        return result

    def fail(self, name, detail, hint):
        # the network admins find the matching entry in their firewall logs by time
        print(f"  {'FAILED':6}  {name:24}  {time.strftime('%H:%M:%S')}  {detail}")
        self.failed = True
        if hint and hint not in self.hints:
            self.hints.append(hint)

    def finish(self):
        print()
        for hint in self.hints:
            print(f"  {hint}")
        print("FAILED" if self.failed else "PASSED")
        return 1 if self.failed else 0


def check_proxy():
    proxies = urllib.request.getproxies()
    if proxies:
        raise Failure(", ".join(f"{scheme} {url}" for scheme, url in proxies.items()),
                      f"{MACHINE}: it's set to use a proxy, the database connection can't go through one")
    return "none"


def check_clock(url):
    request = urllib.request.Request(url, method="HEAD")
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT_SEC, context=unverified())
    except urllib.error.HTTPError as error:
        response = error    # any answer carries the server's time
    skew = time.time() - email.utils.parsedate_to_datetime(response.headers["Date"]).timestamp()
    if abs(skew) > MAX_CLOCK_SKEW_SEC:
        raise Failure(f"{skew:+.0f}s off the server's time",
                      f"{MACHINE}: its clock looks wrong, the cloud can refuse sign in because of it")
    return f"{skew:+.0f}s"


def check_host(host, port, postgres=False):
    """
    Name lookup, then connect, then the TLS handshake, failing at the first layer that breaks. The certificate has
    to come from Azure, anything else means a box on the network opened the connection and re-signed it.
    """
    try:
        ip = socket.gethostbyname(host)
    except OSError as error:
        raise Failure(f"name lookup failed, {error}", f"{NETWORK}: its DNS can't find {host}") from error

    try:
        sock = socket.create_connection((ip, port), timeout=TIMEOUT_SEC)
    except OSError as error:
        raise Failure(f"{ip}:{port} can't connect, {error}",
                      f"{NETWORK}: port {port} to {ip} seems blocked, by this network or by a firewall in front of the "
                      f"server") \
            from error

    with sock:
        try:
            if postgres:
                sock.sendall(POSTGRES_SSL_REQUEST)
                if sock.recv(1) != POSTGRES_SSL_YES:
                    raise Failure(f"{ip}:{port} connects, the server won't use TLS")
            with unverified().wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                version = tls.version()
        except OSError as error:
            raise Failure(f"{ip}:{port} connects, TLS cut off, {type(error).__name__}: {error}",
                          f"{NETWORK}: {ip}:{port} connects but the TLS gets cut, it likely needs allowing "
                          f"without TLS inspection") from error

    name = issuer(der)
    if not any(expected in name for expected in AZURE_ISSUERS):
        raise Failure(f"{ip}:{port} {version}, certificate from {name}",
                      f"{NETWORK}: {name} seems to be inspecting TLS to {host}, it likely needs a decryption bypass")
    return f"{ip}:{port} {version}, certificate from {name}"


def blob_host(cfg):
    """
    The storage account's address, read from the connection string without connecting.
    """
    return urlsplit(ContainerClient.from_connection_string(cfg.storage, cfg.container).url).hostname


def check_container(cfg):
    """
    Upload a test profile, read it back and delete it. Done without the database, so a blocked database port can't
    hide whether storage works.
    """
    container = ContainerClient.from_connection_string(cfg.storage, cfg.container)
    try:
        with container:
            frame = pd.DataFrame({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
            uri = AzureBlobStorageWriter(container, cfg.prefix).write_obj(TEST_PATH, frame)
            name = cfg.prefix + TEST_PATH + OBJECT_EXTENSION
            same = container.download_blob(name).readall().decode() == frame.to_csv(index=False, lineterminator="\n")
            container.delete_blob(name)
    except ClientAuthenticationError as error:
        raise Failure(first_line(error),
                      f"{SETTINGS}: the storage connection string in cred.env looks wrong, or its key was changed") \
            from error
    except ResourceNotFoundError as error:
        raise Failure(first_line(error), f"{SETTINGS}: there's no container named {cfg.container}") from error
    except HttpResponseError as error:
        if error.error_code == STORAGE_NOT_ALLOWED:
            raise Failure(first_line(error),
                          f"{SETTINGS}: the storage account's own firewall seems to turn this site away, add the "
                          "site's public IP in its Networking settings") from error
        raise
    if not same:
        raise ValueError(f"{uri} read back different")
    return f"{uri} written, read back and deleted"


def check_login(cfg, opened):
    try:
        opened.append(create_azure_writer(cfg))
    except pg8000.dbapi.DatabaseError as error:
        # Postgres sends its error as fields, C the code and M the message
        fields = error.args[0] if error.args and isinstance(error.args[0], dict) else {}
        message = fields.get("M", first_line(error))
        if fields.get("C") == PG_BAD_PASSWORD:
            raise Failure(message, f"{SETTINGS}: the database user or password in cred.env looks wrong") from error
        if fields.get("C") == PG_NOT_ALLOWED:
            raise Failure(message, f"{SETTINGS}: the database's own firewall seems to turn this site away, add the "
                                   "site's public IP in the server's Networking settings") from error
        raise Failure(message) from error
    return f"{cfg.db} as {cfg.user}"


def check_tables(writer):
    """
    The version, tables and keys, and a pump and dispense row written and rolled back. Prints its own detail.
    """
    if check_database(writer.db_writer):
        raise Failure("a test write failed, see above")
    return "test rows written and rolled back"


def main():
    parser = argparse.ArgumentParser(description="Check the Azure database and blob container can be reached.")
    parser.add_argument("env", nargs="?", default=DEFAULT_ENV, help="env file, such as cred.env")
    args = parser.parse_args()

    try:
        cfg = AzureConfig.from_env(args.env)
        storage_host = blob_host(cfg)
    except (OSError, KeyError, ValueError, TypeError) as error:
        print(f"  {args.env}: {type(error).__name__}: {error}")
        print("  a setting is missing or written wrong, nothing was tried on the network")
        return 2

    # the machine, its packages, what's being checked, and where and when from
    print(f"{platform.platform()}  Python {platform.python_version()}  {ssl.OPENSSL_VERSION}")
    print(", ".join(f"{name} {package_version(name)}" for name in PACKAGES))
    print(f"{cfg.host}:{cfg.port} {cfg.db} as {cfg.user}, {storage_host}/{cfg.container}/{cfg.prefix}")
    print(f"{socket.gethostname()}  {local_ip()}  {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    report = Report()
    report.step("proxy", check_proxy)
    report.step("clock", check_clock, f"https://{storage_host}")
    report.step(storage_host, check_host, storage_host, HTTPS_PORT)
    report.step(f"database {cfg.port}", check_host, cfg.host, cfg.port, True)
    report.step("container", check_container, cfg)

    opened = []
    report.step("database login", check_login, cfg, opened)
    if opened:
        report.step("database tables", check_tables, opened[0])
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
