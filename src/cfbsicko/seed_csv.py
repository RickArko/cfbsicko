"""Laptop xlsx → CSV extract. Fly loads CSV only (stdlib csv, no openpyxl)."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from cfbsicko.db import connect, transaction
from cfbsicko.parse import SlateGame, map_picks_to_slate, parse_pick_text, parse_slate
from cfbsicko.rules import PickSpec, PickValidationError, default_week1_lock, validate_pick_set

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


class SeedConflictError(ValueError):
    """Week already has games or picks; refuse unless force=True."""


def extract_sheet_to_csv(xlsx_path: Path, out_dir: Path, *, season: int = 2026) -> Path:
    from cfbsicko.sheet import read_master_sheet

    sheet = read_master_sheet(xlsx_path)
    games = parse_slate(sheet.slate_text)
    if not games:
        raise ValueError("No games parsed from slate")
    week_rows = [
        {
            "season": season,
            "week_no": 1,
            "title": "Week 1",
            "lock_at": default_week1_lock(season).isoformat(),
            "status": "open",
        }
    ]
    game_rows = [
        {
            "sort_order": idx,
            "day_label": game.day_label,
            "away": game.away,
            "home": game.home,
            "spread_home": game.spread_home,
            "total": game.total,
        }
        for idx, game in enumerate(games)
    ]
    player_rows = [{"display_name": player.display_name} for player in sheet.players]
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
                    "raw_text": _frozen_raw_text(mapped.raw, mapped.market, mapped.side, game),
                }
            )
    if unmapped:
        raise ValueError("Unmapped picks:\n" + "\n".join(unmapped))
    _validate_seed_rows(game_rows, pick_rows, player_rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "week.csv", WEEK_FIELDS, week_rows)
    _write_csv(out_dir / "games.csv", GAME_FIELDS, game_rows)
    _write_csv(out_dir / "players.csv", PLAYER_FIELDS, player_rows)
    _write_csv(out_dir / "picks.csv", PICK_FIELDS, pick_rows)
    return out_dir


_GLUED_SPREAD = re.compile(r"(?<!\s)([+-]\d+(?:\.\d+)?)\s*$")
_ROW_LABEL = re.compile(r"^pick\s*\d+$", re.IGNORECASE)


def slate_games_from_rows(games: list[dict[str, str]]) -> list[SlateGame]:
    out: list[SlateGame] = []
    for row in games:
        spread_home = float(row["spread_home"])
        if spread_home <= 0:
            favorite = row["home"]
            spread = spread_home
        else:
            favorite = row["away"]
            spread = -spread_home
        out.append(
            SlateGame(
                away=row["away"],
                home=row["home"],
                favorite=favorite,
                spread=spread,
                total=float(row["total"]),
                day_label=row.get("day_label") or "Saturday",
            )
        )
    return out


def _normalize_wide_raw(raw: str) -> str:
    text = " ".join(raw.strip().split())
    if not text or _ROW_LABEL.match(text):
        return ""
    if parse_pick_text(text) is not None:
        return text
    glued = _GLUED_SPREAD.sub(r" \1", text)
    return glued if glued != text else text


def extract_wide_picks(
    wide_path: Path,
    games: list[dict[str, str]],
    players: list[dict[str, str]],
) -> list[dict[str, object]]:
    roster = _validated_player_names([dict(row) for row in players])
    slate = slate_games_from_rows(games)
    with wide_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{wide_path} is empty") from exc
        names = [(cell or "").strip() for cell in header]
        columns: list[tuple[int, str]] = []
        for idx, name in enumerate(names):
            if not name:
                continue
            canon = roster.get(name.casefold())
            if canon is None:
                raise ValueError(f"wide column {name!r} is not in players.csv")
            columns.append((idx, canon))
        by_player: dict[str, list[str]] = {canon: [] for _, canon in columns}
        for row in reader:
            for idx, canon in columns:
                if idx >= len(row):
                    continue
                raw = _normalize_wide_raw(row[idx])
                if raw:
                    by_player[canon].append(raw)
    pick_rows: list[dict[str, object]] = []
    unmapped: list[str] = []
    for name, raws in by_player.items():
        if not raws:
            continue
        report = map_picks_to_slate(raws, slate)
        if report.unmapped:
            unmapped.extend(f"{name}: {raw}" for raw in report.unmapped)
            continue
        for slot, mapped in enumerate(report.mapped, start=1):
            game = slate[mapped.game_index]
            pick_rows.append(
                {
                    "display_name": name,
                    "slot": slot,
                    "away": game.away,
                    "home": game.home,
                    "market": mapped.market,
                    "side": mapped.side,
                    "raw_text": _frozen_raw_text(mapped.raw, mapped.market, mapped.side, game),
                }
            )
    if unmapped:
        raise ValueError("Unmapped picks:\n" + "\n".join(unmapped))
    _validate_seed_rows(games, [dict(row) for row in pick_rows], players)
    return pick_rows


def write_wide_picks_csv(seed_dir: Path, *, wide_name: str = "picks_wide.csv") -> Path:
    games = _read_csv(seed_dir / "games.csv")
    players = _read_csv(seed_dir / "players.csv")
    pick_rows = extract_wide_picks(seed_dir / wide_name, games, players)
    dest = seed_dir / "picks.csv"
    _write_csv(dest, PICK_FIELDS, pick_rows)
    return dest


def games_exist(db_path: Path) -> bool:
    conn = connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]) > 0
    finally:
        conn.close()


def seed_from_csv(seed_dir: Path, db_path: Path, *, force: bool = False) -> SeedResult:
    week = _read_csv(seed_dir / "week.csv")[0]
    games = _read_csv(seed_dir / "games.csv")
    players = _read_csv(seed_dir / "players.csv")
    picks = _read_csv(seed_dir / "picks.csv")
    if not games:
        raise ValueError(f"{seed_dir / 'games.csv'} has no games")
    _validate_seed_rows(games, picks, players)

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
            existing_picks = int(
                conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (week_id,)).fetchone()["n"]
            )
            existing_games = int(
                conn.execute("SELECT COUNT(*) AS n FROM games WHERE week_id = ?", (week_id,)).fetchone()["n"]
            )
            if (existing_picks or existing_games) and not force:
                raise SeedConflictError(
                    f"Week {week_no} already has {existing_games} games and {existing_picks} picks. "
                    "Refusing to replace live data. Re-run with --force only after a backup."
                )
            conn.execute("DELETE FROM week_records WHERE week_id = ?", (week_id,))
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
                name = (row["display_name"] or "").strip()
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
                    raise ValueError(f"pick owner {row['display_name']!r} is not in players.csv")
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

            from cfbsicko.jobs import copy_freeze_overlay, schedule_lock_jobs
            from cfbsicko.leagues import sync_default_league_members
            from cfbsicko.store import get_week

            sync_default_league_members(conn)
            copy_freeze_overlay(conn, week_id)
            seeded = get_week(conn, week_no, season)
            if seeded.get("status") == "open":
                schedule_lock_jobs(conn, seeded)

        return SeedResult(
            users=len(user_ids),
            games=len(games),
            picks=pick_count,
            empty_players=tuple(empty),
        )
    finally:
        conn.close()


def _assert_raw_matches_frozen(raw: str, market: str, side: str, game: dict[str, str]) -> None:
    listed = (
        float(game["total"])
        if market == "total"
        else (float(game["spread_home"]) if side == "home" else -float(game["spread_home"]))
    )
    written = _line_from_raw(raw)
    if written is None:
        return
    if abs(written - listed) > 0.05:
        raise ValueError(
            f"raw_text {raw!r} does not match frozen line {listed:g} ({game['away']} at {game['home']})"
        )


def _line_from_raw(raw: str) -> float | None:
    try:
        return float(str(raw).rsplit(" ", 1)[-1])
    except ValueError:
        return None


def _frozen_raw_text(raw: str, market: str, side: str, game: SlateGame) -> str:
    """Board copy must match the frozen number used to grade."""
    total = float(game.total)
    spread_home = float(game.spread_home)
    away = game.away
    home = game.home
    if market == "total":
        written = None
        try:
            written = float(str(raw).rsplit(" ", 1)[-1])
        except ValueError:
            written = None
        if written is not None and abs(written - total) <= 0.05:
            return raw
        label = "Over" if side == "over" else "Under"
        return f"{away}/{home} {label} {total:g}"
    listed = spread_home if side == "home" else -spread_home
    try:
        written = float(str(raw).rsplit(" ", 1)[-1])
    except ValueError:
        written = None
    if written is not None and abs(written - listed) <= 0.05:
        return raw
    team = home if side == "home" else away
    return f"{team} {listed:+g}"


def _validated_player_names(players: list[dict[str, str]]) -> dict[str, str]:
    roster: dict[str, str] = {}
    for idx, row in enumerate(players):
        name = (row.get("display_name") or "").strip()
        if not name:
            raise ValueError(f"players.csv row {idx + 1} is missing display_name")
        key = name.casefold()
        if key in roster:
            raise ValueError(f"duplicate player {name!r}")
        roster[key] = name
        row["display_name"] = name
    return roster


def _validate_seed_rows(
    games: list[dict[str, str]], picks: list[dict[str, str]], players: list[dict[str, str]]
) -> None:
    game_ids: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(games):
        away = (row.get("away") or "").strip()
        home = (row.get("home") or "").strip()
        if not away or not home:
            raise ValueError(f"games.csv row {idx + 1} is missing away/home")
        try:
            float(row["spread_home"])
            float(row["total"])
            int(row["sort_order"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"games.csv row {idx + 1} has invalid numbers") from exc
        key = (away, home)
        if key in game_ids:
            raise ValueError(f"duplicate game {away} at {home}")
        game_ids[key] = idx + 1
    roster = _validated_player_names(players)
    by_player: dict[str, list[PickSpec]] = defaultdict(list)
    for row in picks:
        name = (row.get("display_name") or "").strip()
        canon = roster.get(name.casefold())
        if canon is None:
            raise ValueError(f"pick owner {name!r} is not in players.csv")
        name = canon
        row["display_name"] = name
        key = ((row.get("away") or "").strip(), (row.get("home") or "").strip())
        if key not in game_ids:
            raise ValueError(f"pick references missing game {key}")
        market = (row.get("market") or "").strip()
        side = (row.get("side") or "").strip()
        try:
            slot = int(row["slot"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name}: invalid slot") from exc
        if market not in {"spread", "total"}:
            raise ValueError(f"{name}: invalid market {market!r}")
        if market == "spread" and side not in {"home", "away"}:
            raise ValueError(f"{name}: spread side must be home or away")
        if market == "total" and side not in {"over", "under"}:
            raise ValueError(f"{name}: total side must be over or under")
        game = next(g for g in games if (g["away"].strip(), g["home"].strip()) == key)
        _assert_raw_matches_frozen(row.get("raw_text") or "", market, side, game)
        by_player[name].append(PickSpec(game_id=game_ids[key], market=market, side=side, slot=slot))
    for name, specs in by_player.items():
        try:
            validate_pick_set(specs)
        except PickValidationError as exc:
            raise ValueError(f"{name}: {exc}") from exc


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
