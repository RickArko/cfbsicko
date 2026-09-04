"""Private leagues: membership, buy-in, and payout settings. Shared weekly slate."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from cfbsicko.config import Config
from cfbsicko.rules import BUY_IN_DOLLARS, EXTRA_OWED, PAYOUT_SHARES

DEFAULT_SLUG = "cfbsicko"
DEFAULT_NAME = "CFB Sicko"

LEAGUES_SQL = """
CREATE TABLE IF NOT EXISTS leagues (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    season INTEGER NOT NULL,
    buy_in INTEGER NOT NULL DEFAULT 75,
    pot_first REAL NOT NULL DEFAULT 0.60,
    pot_second REAL NOT NULL DEFAULT 0.30,
    pot_third REAL NOT NULL DEFAULT 0.10,
    extra_owed INTEGER NOT NULL DEFAULT 75,
    bottom_n INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS league_members (
    league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'player',
    buy_in_paid INTEGER NOT NULL DEFAULT 0,
    settled INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (league_id, user_id)
);
"""


def migrate_leagues(conn: sqlite3.Connection) -> None:
    conn.executescript(LEAGUES_SQL)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(invites)")}
    if "league_id" not in cols:
        conn.execute("ALTER TABLE invites ADD COLUMN league_id INTEGER")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(invites)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE invites ADD COLUMN role TEXT NOT NULL DEFAULT 'player'")
    league_id = _ensure_default_league(conn)
    _migrate_invites_per_league(conn, league_id)
    sync_default_league_members(conn, league_id)


def _unique_index_column_sets(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    sets: list[tuple[str, ...]] = []
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        unique = idx["unique"] if isinstance(idx, sqlite3.Row) else idx[2]
        name = idx["name"] if isinstance(idx, sqlite3.Row) else idx[1]
        if not unique:
            continue
        cols = tuple(
            info["name"] if isinstance(info, sqlite3.Row) else info[2]
            for info in conn.execute(f"PRAGMA index_info({name})")
        )
        if cols:
            sets.append(cols)
    return sets


def _migrate_invites_per_league(conn: sqlite3.Connection, league_id: int) -> None:
    """One pending invite per email+league. UNIQUE(email) overwrote side-league seats."""
    uniques = _unique_index_column_sets(conn, "invites")
    if ("email", "league_id") in uniques:
        return
    conn.execute("UPDATE invites SET league_id = ? WHERE league_id IS NULL", (league_id,))
    conn.executescript(
        """
        CREATE TABLE invites_per_league (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            display_name TEXT,
            token_hash TEXT NOT NULL,
            invited_by INTEGER REFERENCES users(id),
            accepted_at TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            league_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'player',
            UNIQUE (email, league_id)
        );
        INSERT INTO invites_per_league (
            id, email, display_name, token_hash, invited_by, accepted_at,
            expires_at, created_at, league_id, role
        )
        SELECT id, email, display_name, token_hash, invited_by, accepted_at,
            expires_at, created_at, league_id, COALESCE(role, 'player')
        FROM invites;
        DROP TABLE invites;
        ALTER TABLE invites_per_league RENAME TO invites;
        """
    )


def _ensure_default_league(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM leagues WHERE slug = ?", (DEFAULT_SLUG,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO leagues (
            slug, name, season, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEFAULT_SLUG,
            DEFAULT_NAME,
            Config.SEASON,
            BUY_IN_DOLLARS,
            PAYOUT_SHARES[0],
            PAYOUT_SHARES[1],
            PAYOUT_SHARES[2],
            EXTRA_OWED,
            3,
        ),
    )
    return int(cur.lastrowid)


def sync_default_league_members(conn: sqlite3.Connection, league_id: int | None = None) -> None:
    if league_id is None:
        league_id = default_league_id(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO league_members (league_id, user_id, role, buy_in_paid, settled)
        SELECT ?, u.id,
               CASE WHEN u.is_commish THEN 'commish' ELSE 'player' END,
               u.buy_in_paid, u.settled
        FROM users u
        """,
        (league_id,),
    )


def default_league_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM leagues WHERE slug = ?", (DEFAULT_SLUG,)).fetchone()
    if row is None:
        return _ensure_default_league(conn)
    return int(row["id"])


def get_league(conn: sqlite3.Connection, league_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
    if row is None:
        raise KeyError(f"league {league_id}")
    return dict(row)


def list_leagues_for_user(conn: sqlite3.Connection, user: dict[str, Any]) -> list[dict[str, Any]]:
    email = (user.get("email") or "").strip().lower()
    site_admin = bool(user.get("is_commish")) or email in Config.commish_emails()
    if site_admin:
        rows = conn.execute("SELECT * FROM leagues ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            """
            SELECT l.* FROM leagues l
            JOIN league_members m ON m.league_id = l.id
            WHERE m.user_id = ?
            ORDER BY l.id
            """,
            (user["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


def get_membership(conn: sqlite3.Connection, league_id: int, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM league_members WHERE league_id = ? AND user_id = ?",
        (league_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def is_league_commish(conn: sqlite3.Connection, user: dict[str, Any], league_id: int) -> bool:
    email = (user.get("email") or "").strip().lower()
    if user.get("is_commish") or email in Config.commish_emails():
        return True
    member = get_membership(conn, league_id, int(user["id"]))
    return bool(member and member.get("role") == "commish")


def is_member(conn: sqlite3.Connection, league_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id = ?",
        (league_id, user_id),
    ).fetchone()
    return row is not None


def add_member(
    conn: sqlite3.Connection,
    league_id: int,
    user_id: int,
    *,
    role: str = "player",
    buy_in_paid: bool = False,
) -> None:
    if role not in {"player", "commish"}:
        raise ValueError("role must be player or commish")
    conn.execute(
        """
        INSERT INTO league_members (league_id, user_id, role, buy_in_paid)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(league_id, user_id) DO UPDATE SET
            role = CASE WHEN excluded.role = 'commish' THEN 'commish' ELSE league_members.role END
        """,
        (league_id, user_id, role, 1 if buy_in_paid else 0),
    )


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "league"


def _validated_league_settings(
    *,
    name: str,
    buy_in: int,
    pot_first: float,
    pot_second: float,
    pot_third: float,
    extra_owed: int,
    bottom_n: int,
) -> tuple[str, int, float, float, float, int, int]:
    name = name.strip()
    if not name:
        raise ValueError("league name is required")
    if buy_in < 1:
        raise ValueError("buy_in must be at least 1")
    if extra_owed < 0:
        raise ValueError("extra_owed must be >= 0")
    for share, label in ((pot_first, "pot_first"), (pot_second, "pot_second"), (pot_third, "pot_third")):
        if share < 0 or share > 1:
            raise ValueError(f"{label} must be between 0 and 1")
    if abs((pot_first + pot_second + pot_third) - 1.0) > 0.001:
        raise ValueError("payout shares must sum to 1")
    if bottom_n < 0:
        raise ValueError("bottom_n must be >= 0")
    return name, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n


def create_league(
    conn: sqlite3.Connection,
    *,
    name: str,
    created_by: int | None,
    buy_in: int = BUY_IN_DOLLARS,
    pot_first: float = PAYOUT_SHARES[0],
    pot_second: float = PAYOUT_SHARES[1],
    pot_third: float = PAYOUT_SHARES[2],
    extra_owed: int = EXTRA_OWED,
    bottom_n: int = 3,
    season: int | None = None,
) -> dict[str, Any]:
    name, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n = _validated_league_settings(
        name=name,
        buy_in=buy_in,
        pot_first=pot_first,
        pot_second=pot_second,
        pot_third=pot_third,
        extra_owed=extra_owed,
        bottom_n=bottom_n,
    )
    season = season or Config.SEASON
    base = slugify(name)
    slug = base
    n = 2
    while conn.execute("SELECT 1 FROM leagues WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    cur = conn.execute(
        """
        INSERT INTO leagues (
            slug, name, season, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (slug, name, season, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n),
    )
    league_id = int(cur.lastrowid)
    if created_by is not None:
        add_member(conn, league_id, created_by, role="commish")
    for email in Config.commish_emails():
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            add_member(conn, league_id, int(row["id"]), role="commish")
    conn.commit()
    return get_league(conn, league_id)


def update_league(
    conn: sqlite3.Connection,
    league_id: int,
    *,
    name: str | None = None,
    buy_in: int | None = None,
    pot_first: float | None = None,
    pot_second: float | None = None,
    pot_third: float | None = None,
    extra_owed: int | None = None,
    bottom_n: int | None = None,
) -> dict[str, Any]:
    current = get_league(conn, league_id)
    name, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n = _validated_league_settings(
        name=current["name"] if name is None else name,
        buy_in=current["buy_in"] if buy_in is None else buy_in,
        pot_first=current["pot_first"] if pot_first is None else pot_first,
        pot_second=current["pot_second"] if pot_second is None else pot_second,
        pot_third=current["pot_third"] if pot_third is None else pot_third,
        extra_owed=current["extra_owed"] if extra_owed is None else extra_owed,
        bottom_n=current["bottom_n"] if bottom_n is None else bottom_n,
    )
    conn.execute(
        """
        UPDATE leagues SET name = ?, buy_in = ?, pot_first = ?, pot_second = ?, pot_third = ?,
            extra_owed = ?, bottom_n = ?
        WHERE id = ?
        """,
        (name, buy_in, pot_first, pot_second, pot_third, extra_owed, bottom_n, league_id),
    )
    conn.commit()
    return get_league(conn, league_id)


def resolve_league_id(conn: sqlite3.Connection, user: dict[str, Any], requested: int | None) -> int:
    available = {int(row["id"]) for row in list_leagues_for_user(conn, user)}
    if requested is not None:
        if requested not in available:
            raise PermissionError("not a member of that league")
        return requested
    if available:
        default = default_league_id(conn)
        return default if default in available else min(available)
    return default_league_id(conn)
