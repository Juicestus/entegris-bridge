"""
Rows into a Postgres database laid out by the portal's migrations. The same whichever cloud hosts it, each cloud
only says how a connection is opened.
"""

import atexit
import json
import time
from dataclasses import asdict

from pg8000.exceptions import InterfaceError

from writer_base import DBWriter

CONNECTION_ERRORS = (InterfaceError, OSError)
JSON_TYPES = {"json", "jsonb"}

# hack: what this database does differently from our rows. it assigns its own ids, sending them even as null
# overrides them, and it links rows to their pump by its own pump id where our rows carry the serial number
DATABASE_COLUMNS = {"pump_id", "dispense_id", "created_at", "ingested_at"}
PUMP_TABLE = "pump"
PUMP_ID = "pump_id"
SERIAL = "serial_number"

TEST_PATH = "connection_test/test"
TEST_SERIAL = "CONNECTION_TEST"
TEST_TIME = "2026-01-01T00:00:00Z"      # the format render writes times in


class PostgresDBWriter(DBWriter):
    def __init__(self, connect, retries: int = 3):
        self.connect = connect      # opens a new connection, called again whenever one dies
        self.retries = retries
        self.pump_ids = {}          # serial number to pump_id, so each pump is looked up once
        self.column_types = {}      # table to {column: type}, read from the database once
        self.connection = None
        atexit.register(self.close)
        self.reconnect()

    def reconnect(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass                  # old connection is probably already dead
        self.connection = self.connect()

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass

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
        cols = ", ".join(self._ident(c) for c in row)
        placeholders = ", ".join(["%s"] * len(row))
        updates = ", ".join(f"{self._ident(c)} = EXCLUDED.{self._ident(c)}" for c in row if c not in keys)
        cur.execute(f"INSERT INTO {self._ident(table)} ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({', '.join(self._ident(c) for c in keys)}) DO UPDATE SET {updates}",
                    tuple(row.values()))

    def write_row(self, table: str, row: dict, keys: list):
        for attempt in range(self.retries + 1):
            conn = self.connection
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
                    self.reconnect()
                except CONNECTION_ERRORS:
                    pass
            except Exception:
                conn.rollback()                       # bad SQL/data, connection is ok
                raise
            finally:
                cur.close()


def check_database(writer):
    """
    Print the database version, tables and keys, then write a pump and dispense row the way the bridge does and
    roll them back. Returns whether anything failed.
    """
    # imported here so the writer itself never depends on the records
    from render import DispenseRecord, PumpRecord

    connection = writer.connection
    failed = False

    print("\nVersion\n")
    cur = connection.cursor()
    cur.execute("SELECT version()")
    print(f"  {cur.fetchone()[0]}")

    print("\nTables\n")

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

    print("\nKeys\n")
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
    print("\nTest Upsert\n")
    cur = connection.cursor()
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
        connection.rollback()
        cur.close()
    return failed
