import atexit
import os
import time
from dataclasses import dataclass

import pandas as pd
from google.cloud import storage
from google.cloud.sql.connector import Connector
from pg8000.exceptions import InterfaceError

from writer_base import DBWriter

# errors that mean "the connection is broken", not "your SQL is wrong"
CONNECTION_ERRORS = (InterfaceError, OSError)


@dataclass(frozen=True)
class GCPConfig:
    instance: str      # "project:region:instance"
    db: str
    user: str
    password: str
    bucket_name: str

    @classmethod
    def from_env(cls) -> "GCPConfig":
        return cls(
            instance=os.environ["CLOUDSQL_INSTANCE"],
            db=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            bucket_name=os.environ["GCS_BUCKET"],
        )


class GCPConnection:
    """
    Owns the connection to the Cloud SQL and the GCS Bucket
    """
    def __init__(self, cfg: GCPConfig | None = None):
        self.cfg = cfg or GCPConfig.from_env()
        self.connector = Connector()
        self.connection = None
        atexit.register(self.close)
        self.reconnect()
        self.bucket = storage.Client().bucket(self.cfg.bucket_name)

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

    @staticmethod 
    def _ident(name: str) -> str:
        """
        Quote a Postgres identifier
        """
        return '"' + name.replace('"', '""') + '"'

    def _execute(self, sql: str, params: tuple):
        """
        SQL Transaction
        """
        conn = self.gcp.connection
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            conn.commit()
        except CONNECTION_ERRORS:
            raise                                 # dead connection, can't roll back
        except Exception:
            conn.rollback()                       # bad SQL/data, connection is ok
            raise
        finally:
            cur.close()

    def write_row(self, table: str, row: dict):
        if not row:
            raise ValueError("row is empty")

        cols = ", ".join(self._ident(c) for c in row)
        placeholders = ", ".join(["%s"] * len(row))
        sql = f"INSERT INTO {self._ident(table)} ({cols}) VALUES ({placeholders})"
        params = tuple(row.values())

        for attempt in range(self.retries + 1):
            try:
                self._execute(sql, params)
                return
            except CONNECTION_ERRORS:
                if attempt == self.retries:
                    raise
                time.sleep(2 ** attempt)          
                try:
                    self.gcp.reconnect()
                except CONNECTION_ERRORS:
                    pass                          
  

    def write_obj(self, path: str, data: pd.DataFrame):
        self.gcp.bucket.blob(path).upload_from_string(
            data.to_csv(index=False), content_type="text/csv"
        )