"""Laptop xlsx → CSV extract. Fly loads CSV only (stdlib csv, no openpyxl)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from cfbsicko.db import connect, transaction
from cfbsicko.parse import map_picks_to_slate, parse_slate
from cfbsicko.rules import default_week1_lock

WEEK_FIELDS = ("season", "week_no", "title", "lock_at", "status")
GAME_FIELDS = ("sort_order", "day_label", "away", "home", "spread_home", "total")
PLAYER_FIELDS = ("display_name",)
PICK_FIELDS = ("display_name", "slot", "away", "home", "market", "side", "raw_text")


@dataclass(frozen=True)
class SeedResult:
    users: int
    games: int
    picks: int
    empty_players: tuple[str, ...]


def extract_sheet_to_csv(xlsx_path: Path, out_dir: Path, *, season: int = 2026) -> Path:
    from cfbsicko.sheet import read_master_sheet

    sheet = read_master_sheet(xlsx_path)
    games = parse_slate(sheet.slate_text)
    if not games:
        raise ValueError("No games parsed from slate")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "week.csv",
        WEEK_FIELDS,
        [
            {
                "season": season,
                "week_no": 1,
                "title": "Week 1",
                "lock_at": default_week1_lock(season).isoformat(),
                "status": "open",
            }
        ],
    )
    _write_csv(
        out_dir / "games.csv",
        GAME_FIELDS,
        [
            {
                "sort_order": idx,
                "day_label": game.day_label,
                "away": game.away,
                "home": game.home,
                "spread_home": game.spread_home,
                "total": game.total,
            }
            for idx, game in enumerate(games)
        ],
    )
    _write_csv(
        out_dir / "players.csv",
        PLAYER_FIELDS,
        [{"display_name": player.display_name} for player in sheet.players],
    )
    pick_rows: list[dict[str, object]] = []
    unmapped: list[str] = []
    for player in sheet.players:
        filled = [p for p in player.picks if p.strip()]
        if not filled:
            continue
        report = map_picks_to_slate(list(player.picks), games)
        if report.unmapped:
            unmapped.extend(f"{player.display_name}: {raw}" for raw in report.unmapped)
            continue
        for slot, mapped in enumerate(report.mapped, start=1):
            game = games[mapped.game_index]
            pick_rows.append(
                {
                    "display_name": player.display_name,
                    "slot": slot,
                    "away": game.away,
                    "home": game.home,
                    "market": mapped.market,
                    "side": mapped.side,
                    "raw_text": mapped.raw,
                }
            )
    if unmapped:
        raise ValueError("Unmapped picks:\n" + "\n".join(unmapped))
    _write_csv(out_dir / "picks.csv", PICK_FIELDS, pick_rows)
    return out_dir


def seed_from_csv(seed_dir: Path, db_path: Path) -> SeedResult:
    week = _read_csv(seed_dir / "week.csv")[0]
    games = _read_csv(seed_dir / "games.csv")
    players = _read_csv(seed_dir / "players.csv")
    picks = _read_csv(seed_dir / "picks.csv")
    if not games:
        raise ValueError(f"{seed_dir / 'games.csv'} has no games")

    season = int(week["season"])
    week_no = int(week["week_no"])
    conn = connect(db_path)
    empty: list[str] = []
    try:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO weeks (season, week_no, title, lock_at, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(season, week_no) DO UPDATE SET
                    title = excluded.title,
                    lock_at = excluded.lock_at,
                    status = excluded.status
                """,
                (season, week_no, week["title"], week["lock_at"], week.get("status") or "open"),
            )
            week_id = int(
                conn.execute(
                    "SELECT id FROM weeks WHERE season = ? AND week_no = ?",
                    (season, week_no),
                ).fetchone()["id"]
            )
            conn.execute("DELETE FROM picks WHERE week_id = ?", (week_id,))
            conn.execute(
                "DELETE FROM game_results WHERE game_id IN (SELECT id FROM games WHERE week_id = ?)",
                (week_id,),
            )
            conn.execute("DELETE FROM games WHERE week_id = ?", (week_id,))

            game_key: dict[tuple[str, str], int] = {}
            for row in games:
                cur = conn.execute(
                    """
                    INSERT INTO games (week_id, away, home, spread_home, total, day_label, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        week_id,
                        row["away"],
                        row["home"],
                        float(row["spread_home"]),
                        float(row["total"]),
                        row["day_label"],
                        int(row["sort_order"]),
                    ),
                )
                game_key[(row["away"], row["home"])] = int(cur.lastrowid)

            user_ids: dict[str, int] = {}
            for row in players:
                name = row["display_name"]
                existing = conn.execute("SELECT id FROM users WHERE display_name = ?", (name,)).fetchone()
                if existing is None:
                    cur = conn.execute("INSERT INTO users (display_name) VALUES (?)", (name,))
                    user_ids[name] = int(cur.lastrowid)
                else:
                    user_ids[name] = int(existing["id"])

            pick_count = 0
            picked = {row["display_name"] for row in picks}
            for name in user_ids:
                if name not in picked:
                    empty.append(name)
            for row in picks:
                key = (row["away"], row["home"])
                game_id = game_key.get(key)
                if game_id is None:
                    raise ValueError(f"pick references missing game {key}")
                user_id = user_ids.get(row["display_name"])
                if user_id is None:
                    cur = conn.execute(
                        "INSERT INTO users (display_name) VALUES (?)",
                        (row["display_name"],),
                    )
                    user_id = int(cur.lastrowid)
                    user_ids[row["display_name"]] = user_id
                conn.execute(
                    """
                    INSERT INTO picks (user_id, week_id, slot, game_id, market, side, raw_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        week_id,
                        int(row["slot"]),
                        game_id,
                        row["market"],
                        row["side"],
                        row["raw_text"],
                    ),
                )
                pick_count += 1

        return SeedResult(
            users=len(user_ids),
            games=len(games),
            picks=pick_count,
            empty_players=tuple(empty),
        )
    finally:
        conn.close()


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
