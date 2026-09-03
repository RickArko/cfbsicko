from pathlib import Path

from cfbsicko.db import connect
from cfbsicko.import_sheet import import_master_sheet
from cfbsicko.seed_csv import extract_sheet_to_csv, games_exist, seed_from_csv

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
