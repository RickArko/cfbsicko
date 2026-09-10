"""Re-grade Week 1 from frozen picks + final scores; match the published table."""

from __future__ import annotations

import csv
from pathlib import Path

from cfbsicko.db import connect
from cfbsicko.seed_csv import seed_from_csv
from cfbsicko.store import get_week, grade_week, list_games, set_game_result, standings

WEEK1 = Path(__file__).resolve().parents[1] / "seeds" / "2026" / "week-01"


def _rows(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader((WEEK1 / name).open(encoding="utf-8")))


def test_week1_standings_match_published_table(tmp_path):
    db = tmp_path / "locks.db"
    seed_from_csv(WEEK1, db)
    expected = {row["display_name"]: row for row in _rows("standings.csv")}
    assert set(expected) == {
        "Stu",
        "Jack",
        "Billy",
        "Mike",
        "Rick",
        "Wil",
        "Scout",
        "Kenny",
        "Owen",
        "Luke",
        "Joe",
        "Rob",
    }

    conn = connect(db)
    try:
        week = get_week(conn, 1, 2026)
        by_match = {(g["away"], g["home"]): g for g in list_games(conn, week["id"])}
        loaded = 0
        for row in _rows("results.csv"):
            game = by_match.get((row["away"], row["home"]))
            if game is None:
                continue
            set_game_result(
                conn,
                int(game["id"]),
                home_score=int(row["home_score"]),
                away_score=int(row["away_score"]),
                entered_by=None,
                source=row.get("source") or "espn",
            )
            loaded += 1
        assert loaded == 32

        grade_week(conn, 1, season=2026, partial=True)
        table = standings(conn, 2026)["table"]
        computed = {row["display_name"]: row for row in table if row["display_name"] in expected}
        assert len(computed) == 12

        for name, exp in expected.items():
            got = computed[name]
            assert (got["wins"], got["losses"], got["ties"]) == (
                int(exp["wins"]),
                int(exp["losses"]),
                int(exp["ties"]),
            ), name

        ranked = sorted(
            computed.values(),
            key=lambda r: (-r["wins"], r["losses"], -r["ties"], r["display_name"].lower()),
        )
        assert ranked[0]["display_name"] == "Luke"
        assert ranked[0]["wins"] == 4
        assert {r["display_name"] for r in ranked[1:7]} == {
            "Billy",
            "Joe",
            "Mike",
            "Owen",
            "Scout",
            "Stu",
        }
        assert {r["display_name"] for r in ranked[7:10]} == {"Jack", "Rick", "Rob"}
        assert {r["display_name"] for r in ranked[10:]} == {"Kenny", "Wil"}
    finally:
        conn.close()
