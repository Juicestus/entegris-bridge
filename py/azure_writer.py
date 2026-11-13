"""
Rows into Azure Database for PostgreSQL and profiles into Azure Blob Storage. The database is reached with its own
login and the container with one connection string, so everything IT has to hand over fits in the env file. The
names are the ones the portal's env file uses, so both read the same file.

    DATABASE_URL="postgresql://<user>:<password>@<server>.postgres.database.azure.com:5432/<database>"
    AZURE_STORAGE_CONNECTION_STRING="<connection string, account key or SAS>"
    AZURE_STORAGE_CONTAINER=<container>
    BLOB_PREFIX=<optional, put in front of every blob name>
    API_URL=<the proxy's address, for networks where the database can't be reached directly>
    API_KEY=<the secret the proxy checks>
"""

import atexit
import os
import ssl
from dataclasses import dataclass
from functools import partial
from urllib.parse import unquote, urlsplit, urlunsplit

import pandas as pd
import pg8000.dbapi
from azure.storage.blob import ContainerClient, ContentSettings
from dotenv import dotenv_values

from api_writer import APIDBWriter
from postgres_writer import PostgresDBWriter
from writer_base import StorageWriter, Writer

OBJECT_EXTENSION = ".csv"
DEFAULT_PG_PORT = 5432
CONNECT_TIMEOUT_SEC = 30


@dataclass(frozen=True)
class AzureConfig:
    host: str
    port: int
    db: str
    user: str
    password: str
    storage: str        # the storage connection string
    container: str
    prefix: str = ""    # put in front of every blob name, keeps dev and production apart in one container
    api_url: str = ""
    api_key: str = ""

    @classmethod
    def from_env(cls, path=None) -> "AzureConfig":
        """
        From an env file, or from the process environment when there's no file.
        """
        if path is None:
            env = os.environ
        else:
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            env = dotenv_values(path)
        # the user and password are written URL encoded, so a password can hold characters like @ and :
        url = urlsplit(env["DATABASE_URL"])
        return cls(
            host=url.hostname,
            port=url.port or DEFAULT_PG_PORT,
            db=url.path.lstrip("/"),
            user=unquote(url.username),
            password=unquote(url.password),
            storage=env["AZURE_STORAGE_CONNECTION_STRING"],
            container=env["AZURE_STORAGE_CONTAINER"],
            prefix=env.get("BLOB_PREFIX") or "",
            api_url=env.get("API_URL") or "",
            api_key=env.get("API_KEY") or "",
        )


class AzureBlobStorageWriter(StorageWriter):
    def __init__(self, container: ContainerClient, prefix=""):
        self.container = container
        self.prefix = prefix

    def write_obj(self, path: str, data: pd.DataFrame):
        blob = self.container.upload_blob(
            self.prefix + path + OBJECT_EXTENSION, data.to_csv(index=False, lineterminator="\n"),
            overwrite=True, content_settings=ContentSettings(content_type="text/csv"),
        )
        # a SAS connection string puts its key in the blob's url, and that must never be saved in the database
        parts = urlsplit(blob.url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def connect_database(cfg: AzureConfig):
    # Azure only takes encrypted logins, and checking its certificate keeps the password from going anywhere else
    return pg8000.dbapi.connect(
        user=cfg.user, password=cfg.password, host=cfg.host, port=cfg.port, database=cfg.db,
        ssl_context=ssl.create_default_context(), timeout=CONNECT_TIMEOUT_SEC,
    )


def create_container_writer(cfg: AzureConfig) -> AzureBlobStorageWriter:
    container = ContainerClient.from_connection_string(cfg.storage, cfg.container)
    atexit.register(container.close)
    return AzureBlobStorageWriter(container, cfg.prefix)


def create_azure_writer(cfg: AzureConfig) -> Writer:
    """
    The Azure database and blob container, connected.
    """
    return Writer(PostgresDBWriter(partial(connect_database, cfg)), create_container_writer(cfg))


def create_azure_api_writer(cfg: AzureConfig) -> Writer:
    """
    Rows through the proxy, profiles straight into the blob container.
    """
    return Writer(APIDBWriter(cfg.api_url, cfg.api_key), create_container_writer(cfg))
