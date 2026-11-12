import argparse
import atexit
import os
import sys
from dataclasses import dataclass

import pandas as pd
from dotenv import dotenv_values
from google.cloud import storage
from google.cloud.sql.connector import Connector
from google.oauth2 import service_account

from postgres_writer import TEST_PATH, PostgresDBWriter, check_database
from writer_base import StorageWriter, Writer

OBJECT_EXTENSION = ".csv"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@dataclass(frozen=True)
class GCPConfig:
    project: str
    credentials_path: str   # the service account key file
    instance: str      # "project:region:instance"
    db: str
    user: str
    password: str
    bucket_name: str
    prefix: str = ""   # put in front of every object path, keeps dev and production apart in one bucket

    @classmethod
    def from_env(cls, path=None) -> "GCPConfig":
        """
        From an env file, or from the process environment when there's no file.
        """
        if path is None:
            env = os.environ
            credentials_path = os.path.abspath(env["GOOGLE_APPLICATION_CREDENTIALS"])
        else:
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            env = dotenv_values(path)
            # the key file is named relative to the env file, so the bridge can be run from any folder
            credentials_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                                            env["GOOGLE_APPLICATION_CREDENTIALS"])
        return cls(
            project=env["GOOGLE_CLOUD_PROJECT"],
            credentials_path=credentials_path,
            instance=env["INSTANCE_CONNECTION_NAME"],
            db=env["DB_NAME"],
            user=env["DB_USER"],
            password=env["DB_PASSWORD"],
            bucket_name=env["GCS_BUCKET"],
            prefix=env.get("GCS_PREFIX", ""),
        )


class GCSStorageWriter(StorageWriter):
    def __init__(self, bucket, prefix=""):
        self.bucket = bucket
        self.prefix = prefix

    def write_obj(self, path: str, data: pd.DataFrame):
        blob = self.bucket.blob(self.prefix + path + OBJECT_EXTENSION)
        blob.upload_from_string(
            data.to_csv(index=False, lineterminator="\n"), content_type="text/csv"
        )
        return f"gs://{self.bucket.name}/{blob.name}"


def create_gcp_writer(cfg: GCPConfig) -> Writer:
    """
    The Cloud SQL database and the GCS bucket, connected.
    """
    # handed to both clients, so neither goes looking for credentials elsewhere on the machine
    credentials = service_account.Credentials.from_service_account_file(cfg.credentials_path, scopes=SCOPES)
    connector = Connector(credentials=credentials)
    # registered before the database writer registers its own, so the connection closes before the connector
    atexit.register(connector.close)

    def connect():
        return connector.connect(cfg.instance, "pg8000", user=cfg.user, password=cfg.password, db=cfg.db)

    bucket = storage.Client(project=cfg.project, credentials=credentials).bucket(cfg.bucket_name)
    return Writer(PostgresDBWriter(connect), GCSStorageWriter(bucket, cfg.prefix))


def profiles_prefix(cfg, serial):
    # imported here so the writer itself never depends on render
    from render import PROFILES_PATH
    return f"{cfg.prefix}{PROFILES_PATH}/{serial}/"


def show(writer, cfg, serial):
    """
    What the database and bucket hold for one pump.
    """
    cur = writer.db_writer.connection.cursor()
    cur.execute("SELECT pump_id, pump_name, read_at FROM pump WHERE serial_number = %s", (serial,))
    pump = cur.fetchone()
    if pump is None:
        print(f"  no pump row for {serial}")
    else:
        print(f"  pump {serial}, pump_id {pump[0]}, {pump[1]}, read {pump[2]}")
        cur.execute("SELECT cycle_number, triggered_at, header_available, blank_traces, reconstructed, retries, "
                    "trace_object_uri FROM dispense WHERE pump_id = %s ORDER BY cycle_number", (pump[0],))
        rows = cur.fetchall()
        print(f"\n  {len(rows)} dispense rows")
        for cycle, triggered, header, blank, reconstructed, retries, uri in rows:
            print(f"  {cycle} {triggered}, header {header}, blank {blank}, reconstructed {reconstructed}, "
                  f"retries {retries}, {uri}")
    cur.close()

    blobs = list(writer.storage_writer.bucket.list_blobs(prefix=profiles_prefix(cfg, serial)))
    print(f"\n  {len(blobs)} profile objects")
    for blob in blobs:
        print(f"  gs://{cfg.bucket_name}/{blob.name}, {blob.size} bytes")
    return 0


def delete(writer, cfg, serial):
    """
    Remove one pump's rows and profile objects, to clean up after a test.
    """
    connection = writer.db_writer.connection
    cur = connection.cursor()
    try:
        cur.execute("DELETE FROM dispense WHERE pump_id IN (SELECT pump_id FROM pump WHERE serial_number = %s)",
                    (serial,))
        dispense_count = cur.rowcount
        cur.execute("DELETE FROM pump WHERE serial_number = %s", (serial,))
        pump_count = cur.rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
    print(f"  deleted {pump_count} pump rows, {dispense_count} dispense rows")

    blobs = list(writer.storage_writer.bucket.list_blobs(prefix=profiles_prefix(cfg, serial)))
    for blob in blobs:
        blob.delete()
    print(f"  deleted {len(blobs)} profile objects")
    return 0


def main():
    """
    Check the database and bucket in an env file can be reached, without leaving anything behind. Or show or
    delete what's stored for one pump.

        python gcp_writer.py cred.env [--show SERIAL | --delete SERIAL]
    """
    parser = argparse.ArgumentParser(description="Check, show or clean up the cloud database and bucket.")
    parser.add_argument("env", help="env file, such as cred.env")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--show", metavar="SERIAL", help="list the rows and profiles stored for this pump")
    action.add_argument("--delete", metavar="SERIAL", help="delete the rows and profiles stored for this pump")
    args = parser.parse_args()

    cfg = GCPConfig.from_env(args.env)
    print(f"  {cfg.instance} {cfg.db} as {cfg.user}, gs://{cfg.bucket_name}/{cfg.prefix}\n")
    writer = create_gcp_writer(cfg)
    if args.show:
        return show(writer, cfg, args.show)
    if args.delete:
        return delete(writer, cfg, args.delete)
    return check(writer, cfg)


def check(writer, cfg):
    failed = check_database(writer.db_writer)

    print("\nBucket\n")
    frame = pd.DataFrame({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
    try:
        uri = writer.storage_writer.write_obj(TEST_PATH, frame)
        print(f"  uploaded {uri}")
        blob = writer.storage_writer.bucket.blob(cfg.prefix + TEST_PATH + OBJECT_EXTENSION)
        same = blob.download_as_text() == frame.to_csv(index=False, lineterminator="\n")
        print(f"  read back {'OK' if same else 'DIFFERENT'}")
        failed = failed or not same
        blob.delete()
        print("  deleted")
    except Exception as error:
        print(f"  FAILED, {type(error).__name__}: {error}")
        failed = True

    print("\nFAILED" if failed else "\nPASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
