"""Thin score/odds feed. No cfb-data pin. Tests inject a mock."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cfbsicko.config import Config
from cfbsicko.rules import EASTERN

log = logging.getLogger("cfbsicko.feed")

CFBD_BASE = "https://api.collegefootballdata.com"
CFBD_TIMEOUT = 3.0
GetJson = Callable[[str, dict[str, str]], list[dict[str, Any]]]


@dataclass(frozen=True)
class FeedGame:
    away: str
    home: str
    spread_home: float
    total: float
    provider_game_id: str
    kickoff: str | None = None
    day_label: str = "Saturday"
    home_score: int | None = None
    away_score: int | None = None
    status: str = "scheduled"
    period: str | None = None
    clock: str | None = None


class ScoreOddsFeed(Protocol):
    def slate(self, season: int, week_no: int) -> list[FeedGame]: ...

    def odds(self, provider_ids: list[str]) -> list[FeedGame]: ...

    def scores(self, provider_ids: list[str]) -> list[FeedGame]: ...


class EmptyFeed:
    """Production default when no API key is configured. Ticks no-op."""

    def slate(self, season: int, week_no: int) -> list[FeedGame]:
        return []

    def odds(self, provider_ids: list[str]) -> list[FeedGame]:
        return []

    def scores(self, provider_ids: list[str]) -> list[FeedGame]:
        return []


class StaticFeed:
    """In-memory feed for tests and admin ingest fixtures."""

    def __init__(self, games: list[FeedGame] | None = None) -> None:
        self.games = list(games or [])

    def slate(self, season: int, week_no: int) -> list[FeedGame]:
        return list(self.games)

    def odds(self, provider_ids: list[str]) -> list[FeedGame]:
        wanted = set(provider_ids)
        return [game for game in self.games if game.provider_game_id in wanted]

    def scores(self, provider_ids: list[str]) -> list[FeedGame]:
        return self.odds(provider_ids)


def default_feed(api_key: str | None = None) -> ScoreOddsFeed:
    key = Config.CFBD_API_KEY if api_key is None else api_key
    if not key:
        return EmptyFeed()
    return CfbdFeed(key)


class CfbdFeed:
    """Fail-soft CFBD client. Network errors return an empty slate, never raise."""

    def __init__(
        self, api_key: str, *, get_json: GetJson | None = None, timeout: float = CFBD_TIMEOUT
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._get_json = get_json or self._http_get

    def slate(self, season: int, week_no: int) -> list[FeedGame]:
        rows = self._get_json("/lines", {"year": str(season), "week": str(week_no), "seasonType": "regular"})
        return [game for row in rows if (game := self._from_line(row)) is not None]

    def odds(self, provider_ids: list[str]) -> list[FeedGame]:
        wanted = set(provider_ids)
        rows = self._get_json("/lines", {"year": str(Config.SEASON), "seasonType": "regular"})
        return [
            game
            for row in rows
            if (game := self._from_line(row)) is not None and game.provider_game_id in wanted
        ]

    def scores(self, provider_ids: list[str]) -> list[FeedGame]:
        wanted = set(provider_ids)
        rows = self._get_json("/scoreboard", {"year": str(Config.SEASON), "seasonType": "regular"})
        if not rows:
            rows = self._get_json("/games", {"year": str(Config.SEASON), "seasonType": "regular"})
        out: list[FeedGame] = []
        for row in rows:
            game = self._from_score(row)
            if game is not None and game.provider_game_id in wanted:
                out.append(game)
        return out

    def _http_get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{CFBD_BASE}{path}?{urlencode(params)}"
        req = Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except Exception:
            log.exception("cfbd request failed")
            return []
        return payload if isinstance(payload, list) else []

    def _from_line(self, row: dict[str, Any]) -> FeedGame | None:
        spread, total = _consensus_line(row.get("lines") or [])
        if spread is None or total is None:
            return None
        away = _field(row, "awayTeam", "away_team")
        home = _field(row, "homeTeam", "home_team")
        gid = _field(row, "id", "gameId", "game_id")
        if not away or not home or gid is None:
            return None
        kickoff = _field(row, "startDate", "start_date")
        return FeedGame(
            away=str(away),
            home=str(home),
            spread_home=float(spread),
            total=float(total),
            provider_game_id=str(gid),
            kickoff=str(kickoff) if kickoff else None,
            day_label=_day_label(kickoff),
        )

    def _from_score(self, row: dict[str, Any]) -> FeedGame | None:
        away, away_score = _team_field(
            row, ("awayTeam", "away_team"), ("awayPoints", "away_points", "awayScore")
        )
        home, home_score = _team_field(
            row, ("homeTeam", "home_team"), ("homePoints", "home_points", "homeScore")
        )
        gid = _field(row, "id", "gameId", "game_id")
        if not away or not home or gid is None:
            return None
        return FeedGame(
            away=str(away),
            home=str(home),
            spread_home=0.0,
            total=0.0,
            provider_game_id=str(gid),
            home_score=int(home_score) if home_score is not None else None,
            away_score=int(away_score) if away_score is not None else None,
            status=_game_status(row, home_score=home_score, away_score=away_score),
            period=str(_field(row, "period", "currentPeriod") or "") or None,
            clock=str(_field(row, "clock") or "") or None,
        )


def _team_field(
    row: dict[str, Any], team_names: tuple[str, ...], score_names: tuple[str, ...]
) -> tuple[str | None, Any]:
    val = _field(row, *team_names)
    if isinstance(val, dict):
        name = val.get("school") or val.get("displayName") or val.get("name")
        score = val.get("points", val.get("score"))
        return (str(name) if name else None), score
    return (str(val) if val is not None else None), _field(row, *score_names)


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _consensus_line(lines: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not lines:
        return None, None
    preferred = next((row for row in lines if str(row.get("provider") or "").lower() == "consensus"), None)
    if preferred is None:
        preferred = next(
            (row for row in lines if row.get("spread") is not None and row.get("overUnder") is not None),
            lines[0],
        )
    spread = preferred.get("spread")
    total = preferred.get("overUnder")
    if spread is None or total is None:
        return None, None
    return float(spread), float(total)


def _game_status(row: dict[str, Any], *, home_score: Any = None, away_score: Any = None) -> str:
    raw_status = _field(row, "status")
    completed = bool(row.get("completed"))
    if isinstance(raw_status, dict):
        inner = raw_status.get("type") or {}
        completed = completed or bool(raw_status.get("completed") or inner.get("completed"))
        raw_status = inner.get("name") or raw_status.get("description") or ""
    raw = str(raw_status or "").lower()
    if completed or raw in {"final", "completed"}:
        return "final"
    if raw in {"in_progress", "inprogress", "live"} or home_score is not None or away_score is not None:
        return "in_progress"
    return "scheduled"


def _day_label(value: Any) -> str:
    if not value:
        return "Saturday"
    raw = str(value).replace("Z", "+00:00")
    try:
        local = datetime.fromisoformat(raw).astimezone(EASTERN)
    except ValueError:
        return "Saturday"
    return local.strftime("%A")
