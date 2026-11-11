"""
Find where the connection to the cloud breaks, one layer at a time, so a failure says what to ask the network
admins for. Every check runs even after one fails.

    py net_check.py [cred.env]
"""

import argparse
import email.utils
import importlib.metadata
import os
import platform
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.x509.oid import NameOID
from google.auth.transport.requests import AuthorizedSession, Request
from google.cloud import storage
from google.cloud.sql.connector import Connector
from google.oauth2 import service_account

from gcp_writer import SCOPES, GCPConfig

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(HERE, "cred.env")

API_HOSTS = ["oauth2.googleapis.com", "sqladmin.googleapis.com", "storage.googleapis.com"]
HTTPS_PORT = 443
DB_PORT = 3307
TIMEOUT_SEC = 10
# every certificate Google serves, its own sites and the database alike, names Google as the issuer. anything
# else means a box on the network is opening the connection and re-signing it
GOOGLE_ISSUER = "Google"
MAX_CLOCK_SKEW_SEC = 60
PACKAGES = ["cloud-sql-python-connector", "google-auth", "google-cloud-storage", "aiohttp", "pg8000", "cryptography"]
CLOCK_URL = "https://storage.googleapis.com"
SQLADMIN_URL = "https://sqladmin.googleapis.com/v1/projects/{}/instances/{}/connectSettings"
TEST_OBJECT = "connection_test/net_check.txt"
# any outside address works, connecting a UDP socket only picks the route and sends nothing
ROUTE_PROBE = ("8.8.8.8", 80)


class Failure(Exception):
    def __init__(self, detail, hint=None):
        super().__init__(detail)
        self.hint = hint


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
            self.fail(name, f"{type(error).__name__}: {error}", None)
            return None
        print(f"  {'OK':6}  {name:24}  {result}")
        return result

    def fail(self, name, detail, hint):
        # the network admins find the matching entry in their firewall logs by time
        print(f"  {'FAILED':6}  {name:24}  {time.strftime('%H:%M:%S')}  {detail}")
        self.failed = True
        if hint and hint not in self.hints:
            self.hints.append(hint)


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


def check_proxy():
    proxies = urllib.request.getproxies()
    if proxies:
        raise Failure(", ".join(f"{scheme} {url}" for scheme, url in proxies.items()),
                      "this machine is set to use a proxy, the database connection can't go through one")
    return "none"


def check_clock():
    request = urllib.request.Request(CLOCK_URL, method="HEAD")
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT_SEC, context=unverified())
    except urllib.error.HTTPError as error:
        response = error    # any answer carries the server's time
    skew = time.time() - email.utils.parsedate_to_datetime(response.headers["Date"]).timestamp()
    if abs(skew) > MAX_CLOCK_SKEW_SEC:
        raise Failure(f"{skew:+.0f}s off Google's time", "this machine's clock is wrong, Google refuses sign in")
    return f"{skew:+.0f}s"


def check_host(host, port):
    """
    Name lookup, then connect, then the TLS handshake, failing at the first layer that breaks.
    """
    try:
        ip = socket.gethostbyname(host)
    except OSError as error:
        raise Failure(f"name lookup failed, {error}", "the network's DNS can't find Google") from error

    try:
        sock = socket.create_connection((ip, port), timeout=TIMEOUT_SEC)
    except OSError as error:
        raise Failure(f"{ip}:{port} can't connect, {error}",
                      f"the network blocks port {port} to {ip}") from error

    with sock:
        try:
            with unverified().wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                version = tls.version()
        except OSError as error:
            raise Failure(f"{ip}:{port} connects, TLS cut off, {type(error).__name__}: {error}",
                          f"the network lets {ip}:{port} connect but cuts the TLS, it needs allowing "
                          f"without TLS inspection") from error

    name = issuer(der)
    if GOOGLE_ISSUER not in name:
        raise Failure(f"{ip}:{port} {version}, certificate from {name}",
                      f"{name} on the network is inspecting TLS to {host}, it needs a decryption bypass")
    return f"{ip}:{port} {version}, certificate from {name}"


def check_sign_in(credentials):
    credentials.refresh(Request())
    return credentials.service_account_email


def check_instance(cfg, credentials):
    """
    The database's public IP, from the same API the connector asks.
    """
    parts = cfg.instance.split(":")
    response = AuthorizedSession(credentials).get(SQLADMIN_URL.format(parts[0], parts[2]), timeout=TIMEOUT_SEC)
    if response.status_code != 200:
        raise Failure(f"HTTP {response.status_code}, {response.text[:200]}",
                      "the service account can't look up the database, a cloud side permission problem")
    addresses = {entry["type"]: entry["ipAddress"] for entry in response.json().get("ipAddresses", [])}
    return addresses["PRIMARY"]


def check_database(cfg, credentials):
    connector = Connector(credentials=credentials)
    try:
        connection = connector.connect(cfg.instance, "pg8000", user=cfg.user, password=cfg.password, db=cfg.db)
        cur = connection.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        connection.close()
    finally:
        connector.close()
    return f"{cfg.db} as {cfg.user}"


def check_bucket(cfg, credentials):
    bucket = storage.Client(project=cfg.project, credentials=credentials).bucket(cfg.bucket_name)
    blob = bucket.blob(cfg.prefix + TEST_OBJECT)
    blob.upload_from_string("net check", timeout=TIMEOUT_SEC)
    blob.delete(timeout=TIMEOUT_SEC)
    return f"gs://{cfg.bucket_name}/{cfg.prefix}{TEST_OBJECT} written and deleted"


def local_ip():
    """
    The address this machine sends from, which is what the firewall logs show.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(ROUTE_PROBE)
        return sock.getsockname()[0]


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def main():
    parser = argparse.ArgumentParser(description="Find where the connection to the cloud breaks.")
    parser.add_argument("env", nargs="?", default=DEFAULT_ENV, help="env file, such as cred.env")
    args = parser.parse_args()

    cfg = GCPConfig.from_env(args.env)
    print(f"{platform.platform()}  Python {platform.python_version()}  {ssl.OPENSSL_VERSION}")
    print(", ".join(f"{name} {package_version(name)}" for name in PACKAGES))
    print(f"{cfg.instance}, gs://{cfg.bucket_name}/{cfg.prefix}")
    print(f"{socket.gethostname()}  {local_ip()}  {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    report = Report()
    report.step("proxy", check_proxy)
    report.step("clock", check_clock)
    for host in API_HOSTS:
        report.step(host, check_host, host, HTTPS_PORT)

    credentials = service_account.Credentials.from_service_account_file(cfg.credentials_path, scopes=SCOPES)
    report.step("sign in", check_sign_in, credentials)
    ip = report.step("database address", check_instance, cfg, credentials)
    if ip is not None:
        report.step(f"database {DB_PORT}", check_host, ip, DB_PORT)
    report.step("database login", check_database, cfg, credentials)
    report.step("bucket", check_bucket, cfg, credentials)

    print()
    for hint in report.hints:
        print(f"  {hint}")
    print("FAILED" if report.failed else "PASSED")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
