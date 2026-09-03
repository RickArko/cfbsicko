"""Pure league rules: lock clock, pick validation, ATS/OU grade, standings, payouts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

Market = Literal["spread", "total"]
Side = Literal["home", "away", "over", "under"]
PickResult = Literal["pending", "W", "T", "L"]
REQUIRED_PICKS = 5
BUY_IN_DOLLARS = 75
PAYOUT_SHARES = (0.60, 0.30, 0.10)
BOTTOM_N = 3
EXTRA_OWED = 75


class PickValidationError(ValueError):
    """Invalid weekly pick set."""


@dataclass(frozen=True)
class PickSpec:
    game_id: int
    market: Market
    side: Side
    slot: int


@dataclass(frozen=True)
class WeeklyRecord:
    user_id: int
    display_name: str
    wins: int
    ties: int
    losses: int
    buy_in_paid: bool = False

    @property
    def label(self) -> str:
        return f"{self.wins}-{self.ties}-{self.losses}"


@dataclass(frozen=True)
class PayoutPreview:
    paid_count: int
    pot: int
    first: int
    second: int
    third: int
    extra_each_top: int
    extra_each_bottom: int


def is_before_lock(now: datetime, lock_at: datetime) -> bool:
    """True when picks may still be written. Compare in UTC."""
    return _as_utc(now) < _as_utc(lock_at)


def default_week1_lock(season: int = 2026) -> datetime:
    """Week 1 2026 Thursday 6pm ET (Labor Day week)."""
    if season == 2026:
        return datetime(2026, 9, 3, 18, 0, 0, tzinfo=EASTERN)
    # First Thursday of September is a reasonable default for later seasons.
    return datetime(season, 9, 4, 18, 0, 0, tzinfo=EASTERN)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=EASTERN)
    return value.astimezone(ZoneInfo("UTC"))


def validate_pick_set(picks: list[PickSpec]) -> None:
    if len(picks) != REQUIRED_PICKS:
        raise PickValidationError(f"Exactly {REQUIRED_PICKS} picks required, got {len(picks)}")
    slots = [p.slot for p in picks]
    if sorted(slots) != list(range(1, REQUIRED_PICKS + 1)):
        raise PickValidationError("Slots must be 1 through 5 exactly once")
    seen: set[tuple[int, str]] = set()
    for pick in picks:
        if pick.market == "spread" and pick.side not in {"home", "away"}:
            raise PickValidationError("Spread side must be home or away")
        if pick.market == "total" and pick.side not in {"over", "under"}:
            raise PickValidationError("Total side must be over or under")
        key = (pick.game_id, pick.market)
        if key in seen:
            raise PickValidationError("Duplicate game+market is not allowed")
        seen.add(key)


def grade_spread(*, home_score: int, away_score: int, spread_home: float) -> Literal["home", "away", "push"]:
    margin = home_score + spread_home - away_score
    if margin > 0:
        return "home"
    if margin < 0:
        return "away"
    return "push"


def grade_total(*, home_score: int, away_score: int, total: float) -> Literal["over", "under", "push"]:
    combined = home_score + away_score
    if combined > total:
        return "over"
    if combined < total:
        return "under"
    return "push"


def result_for_pick(
    *,
    market: Market,
    side: Side,
    home_score: int,
    away_score: int,
    spread_home: float,
    total: float,
    override: PickResult | None = None,
) -> PickResult:
    if override in {"W", "T", "L"}:
        return override
    if market == "spread":
        winner = grade_spread(home_score=home_score, away_score=away_score, spread_home=spread_home)
        if winner == "push":
            return "T"
        return "W" if winner == side else "L"
    winner = grade_total(home_score=home_score, away_score=away_score, total=total)
    if winner == "push":
        return "T"
    return "W" if winner == side else "L"


def sort_standings(records: list[WeeklyRecord]) -> list[WeeklyRecord]:
    """Season table: wins desc, losses asc, ties desc, name asc."""
    return sorted(records, key=lambda r: (-r.wins, r.losses, -r.ties, r.display_name.lower()))


def payout_preview(paid_count: int) -> PayoutPreview:
    pot = paid_count * BUY_IN_DOLLARS
    first, second, third = (round(pot * share) for share in PAYOUT_SHARES)
    # Keep the pot exact if rounding drifted.
    drift = pot - (first + second + third)
    first += drift
    return PayoutPreview(
        paid_count=paid_count,
        pot=pot,
        first=first,
        second=second,
        third=third,
        extra_each_top=EXTRA_OWED,
        extra_each_bottom=EXTRA_OWED,
    )
