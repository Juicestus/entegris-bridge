"""
Where rendered outputs go. Rows go to tables and profiles go to objects, so moving from local files to a cloud
database and bucket only means writing a new subclass, nothing that renders changes.
"""

from abc import ABC, abstractmethod

import pandas as pd

class DBWriter(ABC):

    @abstractmethod
    def write_row(self, table: str, row: dict, keys: list):
        """
        Insert the row, or replace the one with the same values in the key columns.
        """

    @abstractmethod
    def write_obj(self, path: str, data: pd.DataFrame):
        """
        Store the frame under a slash separated path with no extension, the format is up to the writer. Returns
        where it went, which the dispense row keeps as its link to the profile.
        """
