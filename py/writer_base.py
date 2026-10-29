"""
Where rendered outputs go. Rows go to tables and profiles go to objects, so moving from local files to a cloud
database and bucket only means writing a new subclass, nothing that renders changes.
"""

from abc import ABC, abstractmethod

import pandas as pd

PUMP_TABLE = "pump"
DISPENSE_TABLE = "dispense"

# the columns that pick out one row, the same ones the database would use
TABLE_KEYS = {
    PUMP_TABLE: ["serial_number"],
    DISPENSE_TABLE: ["serial_number", "cycle_number"],
}


class DBWriter(ABC):

    @abstractmethod
    def write_row(self, table: str, row: dict):
        """
        Insert the row, or replace the one with the same key.
        """

    @abstractmethod
    def write_obj(self, path: str, data: pd.DataFrame):
        """
        Store the frame under a slash separated path with no extension, the format is up to the writer. Returns
        where it went, which the dispense row keeps as its link to the profile.
        """
