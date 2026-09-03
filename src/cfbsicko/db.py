"""SQLite connection, WAL, transactional migrations."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    supabase_user_id TEXT UNIQUE,
    email TEXT UNIQUE,
    display_name TEXT NOT NULL,
    is_commish INTEGER NOT NULL DEFAULT 0,
    buy_in_paid INTEGER NOT NULL DEFAULT 0,
    settled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    token_hash TEXT NOT NULL,
    invited_by INTEGER REFERENCES users(id),
    accepted_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weeks (
    id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    week_no INTEGER NOT NULL,
    title TEXT NOT NULL,
    publish_at TEXT,
    lock_at TEXT NOT NULL,
    standings_at TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    UNIQUE (season, week_no)
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    away TEXT NOT NULL,
    home TEXT NOT NULL,
    spread_home REAL NOT NULL,
    total REAL NOT NULL,
    day_label TEXT NOT NULL,
    kickoff TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    market TEXT NOT NULL,
    side TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT 'pending',
    override_result TEXT,
    raw_text TEXT,
    UNIQUE (user_id, week_id, slot),
    UNIQUE (user_id, week_id, game_id, market)
);

CREATE TABLE IF NOT EXISTS game_results (
    game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    entered_by INTEGER REFERENCES users(id),
    entered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS week_records (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    wins INTEGER NOT NULL DEFAULT 0,
    ties INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, week_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS week_snapshots (
    id INTEGER PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=FULL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    checksum = hashlib.sha256(SCHEMA_SQL.encode()).hexdigest()
    conn.executescript(SCHEMA_SQL)
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
            (SCHEMA_VERSION, checksum),
        )
        conn.commit()
        return
    if row["checksum"] != checksum:
        raise RuntimeError("schema_migrations checksum mismatch — refuse to boot")
    from cfbsicko.leagues import migrate_leagues

    migrate_leagues(conn)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
