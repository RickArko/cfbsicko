"""Thin score/odds feed. No cfb-data pin. Tests inject a mock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
