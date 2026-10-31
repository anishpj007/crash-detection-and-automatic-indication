"""
database.py
Lightweight SQLite data-access layer for the Crash Detection System.
No ORM dependency required -- uses Python's built-in sqlite3 module,
so the project runs with zero extra installs beyond requirements.txt.
"""

import sqlite3
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "crash_detection.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they don't exist yet. Safe to call every startup."""
    conn = get_connection()
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def seed_default_admin():
    """Create the initial admin user with a one-time generated password."""
    from werkzeug.security import generate_password_hash

    conn = get_connection()
    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    count = cur.fetchone()["c"]
    if count == 0:
        password = secrets.token_urlsafe(18)
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash(password), "admin"),
        )
        conn.commit()
        print("[database] Initial admin login -> username: admin")
        print(f"[database] Save this one-time password now: {password}")
    conn.close()


# ---------- Query helpers ----------

def fetch_all(query, params=()):
    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_one(query, params=()):
    conn = get_connection()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(query, params=()):
    """For INSERT/UPDATE/DELETE. Returns the lastrowid."""
    conn = get_connection()
    cur = conn.execute(query, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id
