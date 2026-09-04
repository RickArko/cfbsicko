from pathlib import Path

import pytest

from cfbsicko.db import connect
from cfbsicko.import_sheet import import_master_sheet
from cfbsicko.seed_csv import SeedConflictError, extract_sheet_to_csv, games_exist, seed_from_csv

XLSX = Path(__file__).resolve().parents[1] / "data" / "assets" / "CFB Locks MASTER SHEET 2026.xlsx"
COMMITTED = Path(__file__).resolve().parents[1] / "seeds" / "2026" / "week-01"


def test_extract_and_seed_match_xlsx(tmp_path):
    out = tmp_path / "week-01"
    extract_sheet_to_csv(XLSX, out, season=2026)
    assert (out / "games.csv").is_file()
    assert (out / "picks.csv").is_file()

    from_csv = tmp_path / "from-csv.db"
    from_xlsx = tmp_path / "from-xlsx.db"
    csv_result = seed_from_csv(out, from_csv)
    xlsx_result = import_master_sheet(XLSX, from_xlsx, season=2026)
    assert csv_result.users == xlsx_result.users == 12
    assert csv_result.games == xlsx_result.games
    assert csv_result.picks == xlsx_result.picks == 50
    assert set(csv_result.empty_players) == set(xlsx_result.empty_players) == {"Mike", "Rick"}

    conn = connect(from_csv)
    try:
        stu = conn.execute(
            """
            SELECT p.raw_text, p.market, p.side, g.home
            FROM picks p
            JOIN users u ON u.id = p.user_id
            JOIN games g ON g.id = p.game_id
            WHERE u.display_name = 'Stu'
            ORDER BY p.slot
            """
        ).fetchall()
        assert [row["raw_text"] for row in stu] == [
            "Purdue/ISU Under 57.5",
            "SMU -3.5",
            "Kentucky -23.5",
            "Memphis -11.5",
            "Syracuse -34.5",
        ]
        assert stu[0]["market"] == "total"
        assert stu[0]["side"] == "under"
        assert stu[1]["home"] == "Florida State"
    finally:
        conn.close()


def test_committed_week1_seed(tmp_path):
    db = tmp_path / "locks.db"
    assert games_exist(db) is False
    result = seed_from_csv(COMMITTED, db)
    assert result.users == 12
    assert result.games >= 80
    assert result.picks == 50
    assert set(result.empty_players) == {"Mike", "Rick"}
    assert games_exist(db) is True
    conn = connect(db)
    try:
        scout = conn.execute(
            """
            SELECT p.raw_text, p.side, g.total, g.away, g.home
            FROM picks p
            JOIN users u ON u.id = p.user_id
            JOIN games g ON g.id = p.game_id
            WHERE u.display_name = 'Scout' AND p.slot = 4
            """
        ).fetchone()
        assert scout["away"] == "UAB"
        assert scout["home"] == "Illinois"
        assert scout["total"] == 54.5
        assert scout["side"] == "over"
        assert "54.5" in scout["raw_text"]
        assert "57.5" not in scout["raw_text"]
    finally:
        conn.close()


def test_seed_refuses_existing_week_without_force(tmp_path):
    db = tmp_path / "locks.db"
    seed_from_csv(COMMITTED, db)
    with pytest.raises(SeedConflictError, match="already has"):
        seed_from_csv(COMMITTED, db)
    again = seed_from_csv(COMMITTED, db, force=True)
    assert again.picks == 50


def test_seed_rejects_blank_and_unknown_players(tmp_path):
    src = tmp_path / "bad-players"
    src.mkdir()
    for name in ("week.csv", "games.csv"):
        (src / name).write_text((COMMITTED / name).read_text(), encoding="utf-8")
    (src / "picks.csv").write_text(
        "display_name,slot,away,home,market,side,raw_text\n",
        encoding="utf-8",
    )
    (src / "players.csv").write_text("display_name\n   \nStu\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing display_name"):
        seed_from_csv(src, tmp_path / "blank.db")

    (src / "players.csv").write_text("display_name\nStu\nstu\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate player"):
        seed_from_csv(src, tmp_path / "dup.db")

    (src / "players.csv").write_text("display_name\nStu\n", encoding="utf-8")
    (src / "picks.csv").write_text(
        "display_name,slot,away,home,market,side,raw_text\n"
        "NotStu,1,Indiana State,Purdue,total,under,Purdue/ISU Under 57.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"not in players\.csv"):
        seed_from_csv(src, tmp_path / "unknown.db")


def test_seed_rejects_malformed_picks(tmp_path):
    src = tmp_path / "bad"
    src.mkdir()
    for name in ("week.csv", "games.csv", "players.csv"):
        (src / name).write_text((COMMITTED / name).read_text(), encoding="utf-8")
    (src / "picks.csv").write_text(
        "display_name,slot,away,home,market,side,raw_text\n"
        "Stu,1,Indiana State,Purdue,total,under,Purdue/ISU Under 57.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Exactly 5"):
        seed_from_csv(src, tmp_path / "locks.db")


def test_extract_does_not_write_on_unmapped(tmp_path, monkeypatch):
    from cfbsicko import seed_csv as mod

    def boom(_raw, _games):
        from cfbsicko.parse import MapReport

        return MapReport(mapped=[], unmapped=["not a pick"], warnings=[])

    monkeypatch.setattr(mod, "map_picks_to_slate", boom)
    out = tmp_path / "week-01"
    out.mkdir()
    marker = out / "picks.csv"
    marker.write_text("stale\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unmapped"):
        extract_sheet_to_csv(XLSX, out, season=2026)
    assert marker.read_text(encoding="utf-8") == "stale\n"


def test_opposite_signed_spread_does_not_match_frozen():
    from cfbsicko.parse import SlateGame
    from cfbsicko.seed_csv import _assert_raw_matches_frozen, _frozen_raw_text

    game = {
        "away": "SMU",
        "home": "Florida State",
        "spread_home": "-3.5",
        "total": "53.5",
    }
    with pytest.raises(ValueError, match="does not match"):
        _assert_raw_matches_frozen("Florida State +3.5", "spread", "home", game)
    _assert_raw_matches_frozen("Florida State -3.5", "spread", "home", game)

    slate = SlateGame(
        away="SMU",
        home="Florida State",
        favorite="Florida State",
        spread=-3.5,
        total=53.5,
        day_label="Friday",
    )
    assert slate.spread_home == -3.5
    assert _frozen_raw_text("Florida State +3.5", "spread", "home", slate) == "Florida State -3.5"
    assert _frozen_raw_text("Florida State -3.5", "spread", "home", slate) == "Florida State -3.5"
