import argparse
import atexit
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

import pandas as pd
from dotenv import dotenv_values
from google.cloud import storage
from google.cloud.sql.connector import Connector
from google.oauth2 import service_account
from pg8000.exceptions import InterfaceError

from writer_base import DBWriter

CONNECTION_ERRORS = (InterfaceError, OSError)
OBJECT_EXTENSION = ".csv"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

JSON_TYPES = {"json", "jsonb"}

# hack: what this database does differently from our rows. it assigns its own ids, sending them even as null
# overrides them, and it links rows to their pump by its own pump id where our rows carry the serial number
DATABASE_COLUMNS = {"pump_id", "dispense_id", "created_at", "ingested_at"}
PUMP_TABLE = "pump"
PUMP_ID = "pump_id"
SERIAL = "serial_number"


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


class GCPConnection:
    """
    Owns the connection to the Cloud SQL and the GCS Bucket
    """
    def __init__(self, cfg: GCPConfig):
        self.cfg = cfg
        # handed to both clients, so neither goes looking for credentials elsewhere on the machine
        credentials = service_account.Credentials.from_service_account_file(cfg.credentials_path, scopes=SCOPES)
        self.connector = Connector(credentials=credentials)
        self.connection = None
        atexit.register(self.close)
        self.reconnect()
        self.bucket = storage.Client(project=cfg.project, credentials=credentials).bucket(cfg.bucket_name)

    def reconnect(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass                  # old connection is probably already dead
        self.connection = self.connector.connect(
            self.cfg.instance, "pg8000",
            user=self.cfg.user, password=self.cfg.password, db=self.cfg.db,
        )

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass
        self.connector.close()


class GCPDBWriter(DBWriter):
    def __init__(self, gcp: GCPConnection, retries: int = 3):
        self.gcp = gcp
        self.retries = retries
        self.pump_ids = {}          # serial number to pump_id, so each pump is looked up once
        self.column_types = {}      # table to {column: type}, read from the database once
        self.unique_keys = {}       # table to the column sets it has a unique constraint on, read once

    @staticmethod
    def _ident(name: str) -> str:
        """
        Quote a Postgres identifier
        """
        return '"' + name.replace('"', '""') + '"'

    def types(self, cur, table):
        if table not in self.column_types:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = %s", (table,))
            self.column_types[table] = dict(cur.fetchall())
        return self.column_types[table]

    def has_unique(self, cur, table, keys):
        if table not in self.unique_keys:
            cur.execute("SELECT string_agg(kcu.column_name, ',') FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                        "AND tc.table_schema = kcu.table_schema WHERE tc.table_schema = 'public' "
                        "AND tc.table_name = %s AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE') "
                        "GROUP BY tc.constraint_name", (table,))
            self.unique_keys[table] = [set(columns.split(",")) for (columns,) in cur.fetchall()]
        return set(keys) in self.unique_keys[table]

    def pump_id(self, cur, serial):
        if serial not in self.pump_ids:
            cur.execute(f"SELECT {PUMP_ID} FROM {self._ident(PUMP_TABLE)} WHERE {SERIAL} = %s", (serial,))
            found = cur.fetchone()
            if found is None:
                raise ValueError(f"no pump row for serial {serial}")
            self.pump_ids[serial] = found[0]
        return self.pump_ids[serial]

    def prepare(self, cur, table, row, keys):
        """
        Our row and its key as the table takes them: ids the database assigns left out, the serial number swapped
        for the pump id where the table links by pump id, and lists and dicts as JSON where the column is JSON.
        """
        types = self.types(cur, table)
        row = {column: value for column, value in row.items() if column not in DATABASE_COLUMNS}
        if SERIAL in row and SERIAL not in types and PUMP_ID in types:
            row[PUMP_ID] = self.pump_id(cur, row.pop(SERIAL))
            keys = [PUMP_ID if column == SERIAL else column for column in keys]

        for column, value in row.items():
            if types.get(column) in JSON_TYPES and value is not None:
                row[column] = json.dumps(value)
        return row, keys

    def upsert(self, cur, table, row, keys):
        """
        Insert the row, or update the one with the same key. Doesn't commit, so the caller decides.
        """
        row, keys = self.prepare(cur, table, row, keys)
        if not self.has_unique(cur, table, keys):
            self.update_or_insert(cur, table, row, keys)
            return

        cols = ", ".join(self._ident(c) for c in row)
        placeholders = ", ".join(["%s"] * len(row))
        updates = ", ".join(f"{self._ident(c)} = EXCLUDED.{self._ident(c)}" for c in row if c not in keys)
        cur.execute(f"INSERT INTO {self._ident(table)} ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({', '.join(self._ident(c) for c in keys)}) DO UPDATE SET {updates}",
                    tuple(row.values()))

    def update_or_insert(self, cur, table, row, keys):
        """
        The upsert for a table with no unique constraint on its key, which ON CONFLICT needs. Safe while only one
        bridge writes each pump, two writing the same row at the same moment could both insert.
        TODO: dispense is the only one, until the database has UNIQUE (pump_id, cycle_number) on it. this goes
        once nothing needs it
        """
        values = [c for c in row if c not in keys]
        sets = ", ".join(f"{self._ident(c)} = %s" for c in values)
        matches = " AND ".join(f"{self._ident(c)} = %s" for c in keys)
        cur.execute(f"UPDATE {self._ident(table)} SET {sets} WHERE {matches}",
                    tuple(row[c] for c in values) + tuple(row[c] for c in keys))
        if cur.rowcount > 0:
            return

        cols = ", ".join(self._ident(c) for c in row)
        placeholders = ", ".join(["%s"] * len(row))
        cur.execute(f"INSERT INTO {self._ident(table)} ({cols}) VALUES ({placeholders})", tuple(row.values()))

    def write_row(self, table: str, row: dict, keys: list):
        for attempt in range(self.retries + 1):
            conn = self.gcp.connection
            cur = conn.cursor()
            try:
                self.upsert(cur, table, row, keys)
                conn.commit()
                return
            except CONNECTION_ERRORS:
                # dead connection, nothing to roll back. the upsert makes sending it again safe
                if attempt == self.retries:
                    raise
                time.sleep(2 ** attempt)
                try:
                    self.gcp.reconnect()
                except CONNECTION_ERRORS:
                    pass
            except Exception:
                conn.rollback()                       # bad SQL/data, connection is ok
                raise
            finally:
                cur.close()

    def write_obj(self, path: str, data: pd.DataFrame):
        blob = self.gcp.bucket.blob(self.gcp.cfg.prefix + path + OBJECT_EXTENSION)
        blob.upload_from_string(
            data.to_csv(index=False, lineterminator="\n"), content_type="text/csv"
        )
        return f"gs://{self.gcp.cfg.bucket_name}/{blob.name}"




TEST_PATH = "connection_test/test"
TEST_SERIAL = "CONNECTION_TEST"
TEST_TIME = "2026-01-01T00:00:00Z"      # the format render writes times in

def profiles_prefix(cfg, serial):
    # imported here so the writer itself never depends on render
    from render import PROFILES_PATH
    return f"{cfg.prefix}{PROFILES_PATH}/{serial}/"


def show(gcp, serial):
    """
    What the database and bucket hold for one pump.
    """
    cur = gcp.connection.cursor()
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

    blobs = list(gcp.bucket.list_blobs(prefix=profiles_prefix(gcp.cfg, serial)))
    print(f"\n  {len(blobs)} profile objects")
    for blob in blobs:
        print(f"  gs://{gcp.cfg.bucket_name}/{blob.name}, {blob.size} bytes")
    return 0


def delete(gcp, serial):
    """
    Remove one pump's rows and profile objects, to clean up after a test.
    """
    cur = gcp.connection.cursor()
    try:
        cur.execute("DELETE FROM dispense WHERE pump_id IN (SELECT pump_id FROM pump WHERE serial_number = %s)",
                    (serial,))
        dispense_count = cur.rowcount
        cur.execute("DELETE FROM pump WHERE serial_number = %s", (serial,))
        pump_count = cur.rowcount
        gcp.connection.commit()
    except Exception:
        gcp.connection.rollback()
        raise
    finally:
        cur.close()
    print(f"  deleted {pump_count} pump rows, {dispense_count} dispense rows")

    blobs = list(gcp.bucket.list_blobs(prefix=profiles_prefix(gcp.cfg, serial)))
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
    gcp = GCPConnection(cfg)
    if args.show:
        return show(gcp, args.show)
    if args.delete:
        return delete(gcp, args.delete)
    return check(gcp)


def check(gcp):
    # imported here so the writer itself never depends on the records
    from render import DispenseRecord, PumpRecord

    cfg = gcp.cfg
    writer = GCPDBWriter(gcp)
    failed = False

    print("\n * database\n")
    cur = gcp.connection.cursor()
    cur.execute("SELECT version()")
    print(f"  {cur.fetchone()[0]}")
    cur.execute("SELECT table_name, column_name, data_type, is_nullable, column_default IS NOT NULL "
                "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
    tables = {}
    for table, column, data_type, nullable, has_default in cur.fetchall():
        # only the tables we write get their types, the rest would just be noise
        if table in (PumpRecord.TABLE, DispenseRecord.TABLE):
            column = f"{column} {data_type}"
            # a column we must always fill, the database won't accept the row without it
            if nullable == "NO" and not has_default:
                column += " REQUIRED"
        tables.setdefault(table, []).append(column)
    for table, columns in tables.items():
        print(f"  {table}: {', '.join(columns)}")

    print("\n * keys\n")
    cur.execute("SELECT tc.table_name, tc.constraint_type, "
                "string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) "
                "FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE') "
                "GROUP BY tc.table_name, tc.constraint_name, tc.constraint_type ORDER BY tc.table_name")
    for table, kind, columns in cur.fetchall():
        print(f"  {table}: {kind} ({columns})")
    cur.close()

    # both written the way the bridge writes them, twice so the update path runs too, then all rolled back
    print("\n * row fit, rolled back\n")
    cur = gcp.connection.cursor()
    try:
        for record in [PumpRecord(serial_number=TEST_SERIAL),
                       DispenseRecord(serial_number=TEST_SERIAL, cycle_number=0,
                                      triggered_at=TEST_TIME, recorded_at=TEST_TIME,
                                      problems=["test"], blank_traces=[1],
                                      repairs=[{"trace": 1, "byte": 2, "bit": 7}])]:
            for attempt in ("insert", "update"):
                writer.upsert(cur, record.TABLE, asdict(record), record.KEYS)
                print(f"  {record.TABLE} {attempt} OK")
    except Exception as error:
        print(f"  FAILED, {type(error).__name__}: {error}")
        failed = True
    finally:
        gcp.connection.rollback()
        cur.close()

    print("\n * bucket\n")
    frame = pd.DataFrame({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
    try:
        uri = writer.write_obj(TEST_PATH, frame)
        print(f"  uploaded {uri}")
        blob = gcp.bucket.blob(cfg.prefix + TEST_PATH + OBJECT_EXTENSION)
        same = blob.download_as_text() == frame.to_csv(index=False, lineterminator="\n")
        print(f"  read back {'OK' if same else 'DIFFERENT'}")
        failed = failed or not same
        blob.delete()
        print("  deleted")
    except Exception as error:
        print(f"  FAILED, {type(error).__name__}: {error}")
        failed = True

    print("\n  FAILED" if failed else "\n  PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())