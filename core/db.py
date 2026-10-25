import sqlite3
from contextlib import closing
from .config import DB_FILE


def _query(sql, params=()):
    # a short-lived connection per call, so it is safe from any thread
    # (the dashboard runs the pipeline from Streamlit's script thread)
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:  # closing() closes, "conn" commits
        return conn.execute(sql, params).fetchone()


_query("CREATE TABLE IF NOT EXISTS processed_msg(msg_text TEXT PRIMARY KEY)")


def is_msg_exist(msg):
    return _query("SELECT 1 FROM processed_msg WHERE msg_text = ?", (msg,)) is not None


def save_msg(msg):
    _query("INSERT OR IGNORE INTO processed_msg (msg_text) VALUES (?)", (msg,))


def count_processed():
    return _query("SELECT COUNT(*) FROM processed_msg")[0]
