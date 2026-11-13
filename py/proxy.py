"""
Takes the bridge's rows over HTTPS and writes them to the Azure database, for sites that only let web traffic out.
Only the bridge's own row writes, only to its own tables, and only with the key.

Locally it reads cred.env, the same one the bridge uses:

    py proxy.py

On Azure App Service the same settings go in the app's environment variables, and this folder is deployed from git
so cred.env is never uploaded. The app's startup command:

    gunicorn --bind=0.0.0.0 --timeout 600 proxy:app
"""

import hmac
import os
import threading
from functools import partial

from flask import Flask, request

from api_writer import CHECK_PATH, KEY_HEADER, ROWS_PATH
from azure_writer import AzureConfig, connect_database
from postgres_writer import PostgresDBWriter

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "cred.env")
TABLES = {"pump", "dispense"}
LOCAL_PORT = 5000

cfg = AzureConfig.from_env(ENV_PATH if os.path.exists(ENV_PATH) else None)
if not cfg.api_key:
    raise SystemExit("API_KEY is not set, refusing to start without it")

app = Flask(__name__)
# one connection shared by every request, so only one request uses it at a time
lock = threading.Lock()
writer = None


def db_writer():
    """
    Connected on first use, so the app still starts while the database is down and a failed connect is tried again.
    """
    global writer
    if writer is None:
        writer = PostgresDBWriter(partial(connect_database, cfg))
    return writer


def allowed():
    return hmac.compare_digest(request.headers.get(KEY_HEADER, ""), cfg.api_key)


@app.get("/")
def index():
    return "ok"


@app.get(CHECK_PATH)
def check():
    """
    The key and the database connection, without writing anything.
    """
    if not allowed():
        return "wrong or missing key", 401
    try:
        with lock:
            cur = db_writer().connection.cursor()
            cur.execute("SELECT 1")
            cur.close()
    except Exception as error:
        return f"{type(error).__name__}: {error}", 500
    return "ok"


@app.post(ROWS_PATH)
def rows():
    if not allowed():
        return "wrong or missing key", 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return "expected a JSON object", 400
    table, row, keys = body.get("table"), body.get("row"), body.get("keys")
    if table not in TABLES:
        return f"table {table} not allowed", 400
    if not isinstance(row, dict) or not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        return "expected row as an object and keys as a list of column names", 400
    try:
        with lock:
            db_writer().write_row(table, row, keys)
    except Exception as error:
        return f"{type(error).__name__}: {error}", 500
    return "ok"


if __name__ == "__main__":
    app.run(port=LOCAL_PORT)
