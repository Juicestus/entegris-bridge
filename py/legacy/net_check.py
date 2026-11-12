"""
Find where the connection to the cloud breaks, one layer at a time, so a failure says what to ask the network
admins for. Every check runs even after one fails.

    py net_check.py [cred.env]
"""

import argparse
import os
import sys

from google.auth.transport.requests import AuthorizedSession, Request
from google.cloud import storage
from google.cloud.sql.connector import Connector
from google.oauth2 import service_account

from gcp_writer import SCOPES, GCPConfig
from net_tools import TIMEOUT_SEC, Failure, Report, check_clock, check_host, check_proxy, print_header

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(HERE, "cred.env")

API_HOSTS = ["oauth2.googleapis.com", "sqladmin.googleapis.com", "storage.googleapis.com"]
HTTPS_PORT = 443
DB_PORT = 3307
# every certificate Google serves, its own sites and the database alike, names Google as the issuer
GOOGLE_ISSUERS = ["Google"]
PACKAGES = ["cloud-sql-python-connector", "google-auth", "google-cloud-storage", "aiohttp", "pg8000", "cryptography"]
CLOCK_URL = "https://storage.googleapis.com"
SQLADMIN_URL = "https://sqladmin.googleapis.com/v1/projects/{}/instances/{}/connectSettings"
TEST_OBJECT = "connection_test/net_check.txt"


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


def main():
    parser = argparse.ArgumentParser(description="Find where the connection to the cloud breaks.")
    parser.add_argument("env", nargs="?", default=DEFAULT_ENV, help="env file, such as cred.env")
    args = parser.parse_args()

    cfg = GCPConfig.from_env(args.env)
    print_header(PACKAGES, f"{cfg.instance}, gs://{cfg.bucket_name}/{cfg.prefix}")

    report = Report()
    report.step("proxy", check_proxy)
    report.step("clock", check_clock, CLOCK_URL)
    for host in API_HOSTS:
        report.step(host, check_host, host, HTTPS_PORT, GOOGLE_ISSUERS)

    credentials = service_account.Credentials.from_service_account_file(cfg.credentials_path, scopes=SCOPES)
    report.step("sign in", check_sign_in, credentials)
    ip = report.step("database address", check_instance, cfg, credentials)
    if ip is not None:
        report.step(f"database {DB_PORT}", check_host, ip, DB_PORT, GOOGLE_ISSUERS)
    report.step("database login", check_database, cfg, credentials)
    report.step("bucket", check_bucket, cfg, credentials)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
