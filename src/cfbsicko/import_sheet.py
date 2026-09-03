"""Import the master xlsx into SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cfbsicko.db import connect, transaction
from cfbsicko.parse import map_picks_to_slate, parse_slate
from cfbsicko.rules import default_week1_lock
from cfbsicko.sheet import MasterSheet, read_master_sheet


@dataclass(frozen=True)
class ImportResult:
    users: int
    games: int
    picks: int
    empty_players: tuple[str, ...]
    unmapped: tuple[str, ...]
    warnings: tuple[str, ...]


def import_master_sheet(xlsx_path: Path, db_path: Path, *, season: int = 2026) -> ImportResult:
    sheet = read_master_sheet(xlsx_path)
    return write_sheet(sheet, db_path, season=season)


def write_sheet(sheet: MasterSheet, db_path: Path, *, season: int = 2026) -> ImportResult:
    games = parse_slate(sheet.slate_text)
    if not games:
        raise ValueError("No games parsed from slate")

    conn = connect(db_path)
    unmapped: list[str] = []
    warnings: list[str] = []
    empty_players: list[str] = []
    pick_count = 0

    try:
        with transaction(conn):
            lock_at = default_week1_lock(season).isoformat()
            conn.execute(
                """
                INSERT INTO weeks (season, week_no, title, lock_at, status)
                VALUES (?, 1, 'Week 1', ?, 'open')
                ON CONFLICT(season, week_no) DO UPDATE SET
                    title = excluded.title,
                    lock_at = excluded.lock_at,
                    status = excluded.status
                """,
                (season, lock_at),
            )
            week_id = int(
                conn.execute(
                    "SELECT id FROM weeks WHERE season = ? AND week_no = 1",
                    (season,),
                ).fetchone()["id"]
            )
            conn.execute("DELETE FROM picks WHERE week_id = ?", (week_id,))
            conn.execute(
                "DELETE FROM game_results WHERE game_id IN (SELECT id FROM games WHERE week_id = ?)",
                (week_id,),
            )
            conn.execute("DELETE FROM games WHERE week_id = ?", (week_id,))

            game_ids: list[int] = []
            for order, game in enumerate(games):
                cur = conn.execute(
                    """
                    INSERT INTO games (week_id, away, home, spread_home, total, day_label, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (week_id, game.away, game.home, game.spread_home, game.total, game.day_label, order),
                )
                game_ids.append(int(cur.lastrowid))

            for player in sheet.players:
                existing = conn.execute(
                    "SELECT id FROM users WHERE display_name = ?",
                    (player.display_name,),
                ).fetchone()
                if existing is None:
                    cur = conn.execute(
                        "INSERT INTO users (display_name) VALUES (?)",
                        (player.display_name,),
                    )
                    user_id = int(cur.lastrowid)
                else:
                    user_id = int(existing["id"])

                filled = [p for p in player.picks if p.strip()]
                if not filled:
                    empty_players.append(player.display_name)
                    continue
                report = map_picks_to_slate(list(player.picks), games)
                if report.unmapped:
                    unmapped.extend(f"{player.display_name}: {raw}" for raw in report.unmapped)
                    continue
                warnings.extend(report.warnings)
                for slot, mapped in enumerate(report.mapped, start=1):
                    conn.execute(
                        """
                        INSERT INTO picks (user_id, week_id, slot, game_id, market, side, raw_text)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            week_id,
                            slot,
                            game_ids[mapped.game_index],
                            mapped.market,
                            mapped.side,
                            mapped.raw,
                        ),
                    )
                    pick_count += 1

        if unmapped:
            raise UnmappedPicksError(tuple(unmapped), tuple(warnings))

        return ImportResult(
            users=len(sheet.players),
            games=len(games),
            picks=pick_count,
            empty_players=tuple(empty_players),
            unmapped=(),
            warnings=tuple(warnings),
        )
    finally:
        conn.close()


class UnmappedPicksError(ValueError):
    def __init__(self, unmapped: tuple[str, ...], warnings: tuple[str, ...]):
        self.unmapped = unmapped
        self.warnings = warnings
        super().__init__("Unmapped picks:\n" + "\n".join(unmapped))
