"""Additive live-locks schema. Never edit SCHEMA_SQL."""

from __future__ import annotations

import sqlite3

GAME_COLUMNS = (
    ("provider_game_id", "TEXT"),
    ("market_spread_home", "REAL"),
    ("market_total", "REAL"),
    ("market_updated_at", "TEXT"),
    ("market_source", "TEXT"),
)

RESULT_COLUMNS = (
    ("status", "TEXT NOT NULL DEFAULT 'final'"),
    ("period", "TEXT"),
    ("clock", "TEXT"),
    ("updated_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
)

LIVE_TABLES = """
CREATE TABLE IF NOT EXISTS line_ticks (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL DEFAULT (datetime('now')),
    spread_home REAL NOT NULL,
    total REAL NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (game_id, captured_at, source)
);

CREATE TABLE IF NOT EXISTS pick_revisions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY,
    week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    run_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT,
    last_error TEXT,
    UNIQUE (week_id, kind)
);

CREATE TABLE IF NOT EXISTS mail_outbox (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    week_id INTEGER REFERENCES weeks(id) ON DELETE CASCADE,
    league_id INTEGER,
    to_email TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    send_after TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (kind, week_id, to_email, dedupe_key)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    href TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_line_ticks_game ON line_ticks (game_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON mail_outbox (sent_at, send_after);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON scheduled_jobs (status, run_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, read_at);
"""


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    existing = _column_names(conn, table)
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def migrate_live(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "games", GAME_COLUMNS)
    _add_columns(conn, "game_results", RESULT_COLUMNS)
    conn.executescript(LIVE_TABLES)
    conn.execute(
        "UPDATE scheduled_jobs SET locked_at = NULL WHERE status = 'pending' AND locked_at IS NOT NULL"
    )
