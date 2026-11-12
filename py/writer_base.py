"""
Writer to write rendered outputs 
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

import pandas as pd


class DBWriter(ABC):

    @abstractmethod
    def write_row(self, table: str, row: dict, keys: list):
        """
        Insert the row, or replace the one with the same values in the key columns.
        """


class StorageWriter(ABC):

    @abstractmethod
    def write_obj(self, path: str, data: pd.DataFrame):
        """
        Store the frame under a slash separated path with no extension, the format is up to the writer. Returns
        where it went, which the dispense row keeps as its link to the profile.
        """


class Writer(NamedTuple):
    """
    We need to keep both writers along with eachother. 
    """
    db_writer: DBWriter
    storage_writer: StorageWriter
