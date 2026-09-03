from pathlib import Path

from cfbsicko.db import connect
from cfbsicko.import_sheet import import_master_sheet

XLSX = Path(__file__).resolve().parents[1] / "data" / "assets" / "CFB Locks MASTER SHEET 2026.xlsx"


def test_import_week1_sheet(tmp_path):
    db_path = tmp_path / "locks.db"
    result = import_master_sheet(XLSX, db_path, season=2026)
    assert result.users == 12
    assert result.games >= 80
    assert result.picks == 50
    assert set(result.empty_players) == {"Mike", "Rick"}
    assert result.unmapped == ()

    conn = connect(db_path)
    try:
        names = [row["display_name"] for row in conn.execute("SELECT display_name FROM users ORDER BY id")]
        assert names == [
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
        ]
        empty = conn.execute(
            """
            SELECT u.display_name, COUNT(p.id) AS n
            FROM users u
            LEFT JOIN picks p ON p.user_id = u.id
            GROUP BY u.id
            HAVING n = 0
            """
        ).fetchall()
        assert {row["display_name"] for row in empty} == {"Mike", "Rick"}
        stu = conn.execute(
            """
            SELECT p.raw_text, p.market, p.side, g.home, g.away
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
