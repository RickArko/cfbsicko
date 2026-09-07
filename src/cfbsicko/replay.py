"""Replay Week 1 wide picks through save_picks. Local SQLite only."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from cfbsicko.db import connect
from cfbsicko.leagues import sync_default_league_members
from cfbsicko.rules import PickSpec
from cfbsicko.seed_csv import SeedConflictError, _read_csv, extract_wide_picks, seed_from_csv
from cfbsicko.store import get_week, parse_lock_at, publish_slate, save_picks, update_week, write_snapshot


class ReplayPathError(ValueError):
    """Refusing a Fly or warehouse database path."""


@dataclass(frozen=True)
class ReplayResult:
    users: int
    picks: int
    backup: Path | None
    week_status: str


def assert_local_replay_path(db_path: Path) -> Path:
    path = db_path.expanduser().resolve()
    raw = str(path).replace("\\", "/")
    if raw == "/data/locks.db" or raw.endswith("/data/locks.db"):
        raise ReplayPathError(f"refusing Fly database path {path}")
    if "cfb_data" in raw and "cfbsicko" not in raw:
        raise ReplayPathError(
            f"DATABASE_PATH={path} looks like the fantasy warehouse. Use ~/.cfbsicko/locks.db."
        )
    return path


def _backup_db(db_path: Path) -> Path | None:
    if not db_path.is_file():
        return None
    dest = db_path.with_name(db_path.name + ".pre-replay")
    shutil.copy2(db_path, dest)
    return dest


def replay_week1(
    seed_dir: Path,
    db_path: Path,
    *,
    force: bool = False,
) -> ReplayResult:
    db_path = assert_local_replay_path(db_path)
    backup = _backup_db(db_path)
    week_meta = _read_csv(seed_dir / "week.csv")[0]
    games = _read_csv(seed_dir / "games.csv")
    players = _read_csv(seed_dir / "players.csv")
    pick_rows = extract_wide_picks(seed_dir / "picks_wide.csv", games, players)
    week_no = int(week_meta["week_no"])
    season = int(week_meta["season"])

    conn = connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM weeks WHERE season = ? AND week_no = ?",
            (season, week_no),
        ).fetchone()
        picks_n = 0
        games_n = 0
        if existing is not None:
            picks_n = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (existing["id"],)
                ).fetchone()["n"]
            )
            games_n = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM games WHERE week_id = ?", (existing["id"],)
                ).fetchone()["n"]
            )
            if picks_n and not force:
                raise SeedConflictError(
                    f"Week {week_no} already has {picks_n} picks. Re-run with --force only after a backup."
                )
        need_seed = existing is None or games_n == 0
    finally:
        conn.close()

    if need_seed:
        seed_from_csv(seed_dir, db_path, force=True)

    conn = connect(db_path)
    try:
        week_id = int(
            conn.execute(
                "SELECT id FROM weeks WHERE season = ? AND week_no = ?",
                (season, week_no),
            ).fetchone()["id"]
        )
        conn.execute("DELETE FROM week_records WHERE week_id = ?", (week_id,))
        conn.execute("DELETE FROM picks WHERE week_id = ?", (week_id,))
        conn.execute("DELETE FROM pick_revisions WHERE week_id = ?", (week_id,))
        conn.execute(
            "UPDATE weeks SET status = 'open' WHERE season = ? AND week_no = ?",
            (season, week_no),
        )
        conn.commit()

        user_ids: dict[str, int] = {}
        for row in players:
            name = (row["display_name"] or "").strip()
            found = conn.execute("SELECT id FROM users WHERE display_name = ?", (name,)).fetchone()
            if found is None:
                cur = conn.execute("INSERT INTO users (display_name) VALUES (?)", (name,))
                user_ids[name] = int(cur.lastrowid)
            else:
                user_ids[name] = int(found["id"])
        sync_default_league_members(conn)
        conn.commit()

        week = get_week(conn, week_no, season)
        now = parse_lock_at(week["lock_at"]) - timedelta(seconds=1)
        game_key = {
            (row["away"], row["home"]): int(row["id"])
            for row in conn.execute("SELECT id, away, home FROM games WHERE week_id = ?", (week_id,))
        }
        by_player: dict[str, list[dict[str, object]]] = {}
        for row in pick_rows:
            by_player.setdefault(str(row["display_name"]), []).append(row)
        for name, rows in by_player.items():
            specs = [
                PickSpec(
                    game_id=game_key[(str(row["away"]), str(row["home"]))],
                    market=row["market"],  # validated by extract_wide_picks
                    side=row["side"],
                    slot=int(row["slot"]),
                )
                for row in rows
            ]
            save_picks(conn, user_id=user_ids[name], week=week, picks=specs, now=now)

        update_week(conn, week_no, status="locked", season=season)
        write_snapshot(conn, week_id, "lock")
        locked = get_week(conn, week_no, season)
        return ReplayResult(
            users=len(user_ids),
            picks=len(pick_rows),
            backup=backup,
            week_status=str(locked["status"]),
        )
    finally:
        conn.close()


def publish_week2_rehearsal(seed_dir: Path, db_path: Path) -> dict:
    """Open a local Week 2 slate. Does not rewrite Week 1 picks."""
    db_path = assert_local_replay_path(db_path)
    week = _read_csv(seed_dir / "week.csv")[0]
    slate = (seed_dir / "slate.txt").read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        return publish_slate(
            conn,
            week_no=int(week["week_no"]),
            slate_text=slate,
            lock_at=week["lock_at"],
            season=int(week["season"]),
            title=week.get("title") or "Week 2",
        )
    finally:
        conn.close()
