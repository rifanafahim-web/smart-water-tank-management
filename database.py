"""
database.py - everything that talks to SQLite.
app.py never writes SQL. It only calls the functions in this file.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config


# ---------- Time helpers ----------

def utc_now_iso():
    """Current time in UTC as text, e.g. '2026-10-27T09:15:30+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seconds_since(iso_text):
    """How many seconds have passed since a saved time."""
    then = datetime.fromisoformat(iso_text)
    return (datetime.now(timezone.utc) - then).total_seconds()


# ---------- Connection helper ----------

@contextmanager
def db_connection():
    """
    Opens the database, and always closes it afterwards.
    If everything works the changes are saved (commit).
    If an error happens the changes are undone (rollback).
    Use it like:  with db_connection() as conn: ...
    """
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row      # lets us read columns by name
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Creates the two tables if they do not exist yet."""
    with db_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id     TEXT NOT NULL,
                level_percent REAL NOT NULL
                              CHECK (level_percent >= 0 AND level_percent <= 100),
                motor_status  TEXT NOT NULL CHECK (motor_status IN ('ON', 'OFF')),
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refill_requests (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                status           TEXT NOT NULL
                                 CHECK (status IN ('pending', 'approved', 'rejected',
                                                   'completed', 'expired')),
                level_at_request REAL NOT NULL,
                created_at       TEXT NOT NULL,
                decided_at       TEXT,   -- when the user pressed Approve or Reject
                closed_at        TEXT    -- when the system finished or expired the request
            );
        """)


# ---------- Readings ----------

def save_reading(device_id, level_percent, motor_status):
    """Saves one reading and returns it as a dictionary."""
    with db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO readings (device_id, level_percent, motor_status, created_at) "
            "VALUES (?, ?, ?, ?)",
            (device_id, level_percent, motor_status, utc_now_iso()),
        )
        row = conn.execute("SELECT * FROM readings WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def get_latest_reading():
    """The newest reading, or None if there are no readings yet."""
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM readings ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def get_recent_readings(limit):
    """The newest readings first (a list of dictionaries)."""
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- Refill requests ----------

def get_latest_refill_request():
    """The newest refill request (any status), or None."""
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM refill_requests ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def create_refill_request(level_at_request):
    """Creates a new request with status 'pending' (waiting for the user)."""
    with db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO refill_requests (status, level_at_request, created_at) "
            "VALUES ('pending', ?, ?)",
            (level_at_request, utc_now_iso()),
        )
        row = conn.execute(
            "SELECT * FROM refill_requests WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def decide_refill_request(request_id, new_status):
    """
    Saves the user's decision ('approved' or 'rejected').
    The UPDATE only works while the status is still 'pending'.
    So if two clicks arrive at the same time, only the first one wins.
    Returns True if this call made the change, False if it did not.
    """
    with db_connection() as conn:
        cursor = conn.execute(
            "UPDATE refill_requests SET status = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (new_status, utc_now_iso(), request_id),
        )
        return cursor.rowcount == 1


def complete_refill_request(request_id):
    """Marks an approved refill as finished (the tank reached the high level)."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE refill_requests SET status = 'completed', closed_at = ? "
            "WHERE id = ? AND status = 'approved'",
            (utc_now_iso(), request_id),
        )


def expire_pending_requests():
    """If the water level recovers by itself, a waiting request is no longer needed."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE refill_requests SET status = 'expired', closed_at = ? "
            "WHERE status = 'pending'",
            (utc_now_iso(),),
        )
