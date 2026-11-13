"""
Rows sent over HTTPS to the proxy in the proxy folder, which writes them to the database. For networks that only
let web traffic out, where the database's own port is blocked.
"""

import json
import time
import urllib.error
import urllib.request

from writer_base import DBWriter

ROWS_PATH = "/rows"
CHECK_PATH = "/check"
KEY_HEADER = "X-API-Key"
TIMEOUT_SEC = 30
# what the proxy's host answers while it is starting up or restarting, worth waiting out
RETRY_STATUSES = {502, 503, 504}


class APIDBWriter(DBWriter):
    def __init__(self, url: str, key: str, retries: int = 3):
        if not url or not key:
            raise ValueError("API_URL and API_KEY are both needed")
        self.url = url.rstrip("/")
        self.key = key
        self.retries = retries

    def write_row(self, table: str, row: dict, keys: list):
        # times and anything else JSON has no type for go as text, which the database reads back the same
        body = json.dumps({"table": table, "row": row, "keys": keys}, default=str).encode()
        request = urllib.request.Request(self.url + ROWS_PATH, data=body, method="POST",
                                         headers={"Content-Type": "application/json", KEY_HEADER: self.key})
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SEC):
                    return
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace").strip()
                if error.code not in RETRY_STATUSES or attempt == self.retries:
                    raise RuntimeError(f"{error.code} {detail}") from error
            except OSError:
                # no answer at all, the upsert makes sending it again safe
                if attempt == self.retries:
                    raise
            time.sleep(2 ** attempt)
