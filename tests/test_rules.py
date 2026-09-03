from datetime import datetime, timedelta

import pytest

from cfbsicko.rules import (
    EASTERN,
    PickSpec,
    PickValidationError,
    WeeklyRecord,
    default_week1_lock,
    grade_spread,
    grade_total,
    is_before_lock,
    payout_preview,
    result_for_pick,
    sort_standings,
    validate_pick_set,
)


def test_exactly_five_picks():
    picks = [PickSpec(game_id=i, market="spread", side="home", slot=i) for i in range(1, 6)]
    validate_pick_set(picks)
    with pytest.raises(PickValidationError):
        validate_pick_set(picks[:4])
    with pytest.raises(PickValidationError):
        validate_pick_set([*picks, PickSpec(7, "spread", "home", 6)])


def test_duplicate_game_market_rejected_but_spread_and_total_ok():
    dup = [
        PickSpec(1, "spread", "home", 1),
        PickSpec(1, "spread", "away", 2),
        PickSpec(2, "spread", "home", 3),
        PickSpec(3, "total", "over", 4),
        PickSpec(4, "total", "under", 5),
    ]
    with pytest.raises(PickValidationError):
        validate_pick_set(dup)
    ok = [
        PickSpec(1, "spread", "home", 1),
        PickSpec(1, "total", "over", 2),
        PickSpec(2, "spread", "away", 3),
        PickSpec(3, "total", "under", 4),
        PickSpec(4, "spread", "home", 5),
    ]
    validate_pick_set(ok)


def test_lock_clock_exclusive():
    lock = default_week1_lock(2026)
    assert is_before_lock(lock - timedelta(seconds=1), lock)
    assert not is_before_lock(lock, lock)
    naive = datetime(2026, 9, 3, 18, 0, 0)
    assert not is_before_lock(naive.replace(tzinfo=EASTERN), lock)


def test_ats_and_total_grade():
    assert grade_spread(home_score=24, away_score=17, spread_home=-6.5) == "home"
    assert grade_spread(home_score=24, away_score=17, spread_home=-7.0) == "push"
    assert grade_spread(home_score=24, away_score=17, spread_home=-7.5) == "away"
    assert grade_total(home_score=24, away_score=27, total=50.5) == "over"
    assert grade_total(home_score=24, away_score=27, total=51.0) == "push"
    assert grade_total(home_score=24, away_score=27, total=51.5) == "under"


def test_result_override_wins():
    auto = result_for_pick(
        market="spread",
        side="home",
        home_score=10,
        away_score=3,
        spread_home=-3.5,
        total=40.5,
    )
    assert auto == "W"
    forced = result_for_pick(
        market="spread",
        side="home",
        home_score=10,
        away_score=3,
        spread_home=-3.5,
        total=40.5,
        override="L",
    )
    assert forced == "L"


def test_standings_sort_and_payout_uses_paid_count():
    rows = [
        WeeklyRecord(1, "Zoe", 8, 0, 2, True),
        WeeklyRecord(2, "Ann", 8, 1, 1, True),
        WeeklyRecord(3, "Bob", 7, 0, 3, False),
    ]
    ranked = sort_standings(rows)
    assert [r.display_name for r in ranked] == ["Ann", "Zoe", "Bob"]
    preview = payout_preview(paid_count=10)
    assert preview.pot == 750
    assert preview.first + preview.second + preview.third == 750
    assert preview.first == 450
    assert preview.second == 225
    assert preview.third == 75
    twelve = payout_preview(12)
    assert twelve.pot == 900
    assert twelve.first == 540
