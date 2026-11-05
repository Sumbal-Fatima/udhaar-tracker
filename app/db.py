import os
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    village TEXT,
    language TEXT NOT NULL DEFAULT 'roman_urdu',   -- urdu | roman_urdu | english
    crop TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount REAL NOT NULL,
    issued_on TEXT NOT NULL,
    due_on TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount REAL NOT NULL,
    paid_on TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    action TEXT NOT NULL,          -- remind_now | wait | escalate
    risk TEXT NOT NULL,            -- low | medium | high
    tone TEXT NOT NULL,            -- friendly | firm | respectful
    reason TEXT NOT NULL,
    source TEXT NOT NULL,          -- ai | rule
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    language TEXT NOT NULL,
    tone TEXT NOT NULL,
    message TEXT NOT NULL,
    amount_due REAL NOT NULL,
    amount_check_ok INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | sent
    created_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    source_text TEXT NOT NULL,
    summary TEXT,
    amount REAL,
    promised_date TEXT,
    condition TEXT,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | rejected
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    reason TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',     -- open | decided
    decision TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "shop_name": "Al-Madina Agri Traders",
    "escalate_threshold": "150000",
}


def connect() -> sqlite3.Connection:
    path = config.database_path()
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS[key]
