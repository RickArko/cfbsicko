from datetime import datetime
from pathlib import Path

import pytest
from conftest import mint_token
from fastapi.testclient import TestClient

from cfbsicko.app import create_app
from cfbsicko.cli import main
from cfbsicko.db import connect
from cfbsicko.replay import ReplayPathError, assert_local_replay_path, publish_week2_rehearsal, replay_week1
from cfbsicko.rules import EASTERN
from cfbsicko.seed_csv import SeedConflictError, seed_from_csv
from cfbsicko.store import board, current_week, get_week

WEEK1 = Path(__file__).resolve().parents[1] / "seeds" / "2026" / "week-01"
WEEK2 = Path(__file__).resolve().parents[1] / "seeds" / "2026" / "week-02"


def test_refuse_fly_and_warehouse_paths(tmp_path):
    with pytest.raises(ReplayPathError, match="Fly"):
        assert_local_replay_path(Path("/data/locks.db"))
    with pytest.raises(ReplayPathError, match="warehouse"):
        assert_local_replay_path(tmp_path / "cfb_data" / "cfb.db")
    assert main(["replay-week1", "--db-path", "/data/locks.db", "--seed-dir", str(WEEK1)]) == 2
    assert main(["publish-week2", "--db-path", "/data/locks.db", "--seed-dir", str(WEEK2)]) == 2


def test_replay_refuses_existing_picks_without_force(tmp_path):
    db = tmp_path / "locks.db"
    seed_from_csv(WEEK1, db)
    with pytest.raises(SeedConflictError, match="already has"):
        replay_week1(WEEK1, db)
    result = replay_week1(WEEK1, db, force=True)
    assert result.picks == 60
    assert result.week_status == "locked"
    assert result.backup is not None
    assert result.backup.is_file()


def test_replay_week1_locks_board(tmp_path):
    db = tmp_path / "locks.db"
    result = replay_week1(WEEK1, db)
    assert result.users == 12
    assert result.picks == 60
    assert result.week_status == "locked"
    assert result.backup is None

    conn = connect(db)
    try:
        week = get_week(conn, 1, 2026)
        assert week["status"] == "locked"
        picks_n = int(
            conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (week["id"],)).fetchone()["n"]
        )
        assert picks_n == 60
        empty = conn.execute(
            """
            SELECT u.display_name
            FROM users u
            LEFT JOIN picks p ON p.user_id = u.id AND p.week_id = ?
            GROUP BY u.id
            HAVING COUNT(p.id) = 0
            """,
            (week["id"],),
        ).fetchall()
        assert empty == []
        lock_snap = conn.execute(
            "SELECT kind FROM week_snapshots WHERE week_id = ? AND kind = 'lock'",
            (week["id"],),
        ).fetchone()
        assert lock_snap is not None
        revealed = board(conn, week, datetime(2026, 9, 3, 18, 0, 0, tzinfo=EASTERN))
        assert revealed is not None
        assert len(revealed) >= 12
        filled = [row for row in revealed if row["picks"]]
        assert len(filled) == 12
        assert all(len(row["picks"]) == 5 for row in filled)
    finally:
        conn.close()

    app = create_app(
        db_path=db,
        now_fn=lambda: datetime(2026, 9, 4, 12, 0, 0, tzinfo=EASTERN),
        mail_send=lambda *a, **k: "smtp",
    )
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {mint_token('commish-sub', 'commish@example.com')}"}
        week1 = client.get("/api/weeks/1", headers=headers)
        assert week1.status_code == 200, week1.text
        assert week1.json()["week"]["status"] == "locked"
        assert week1.json()["board"] is not None
        assert week1.json()["locked"] is True


def test_publish_week2_leaves_week1_locked(tmp_path):
    db = tmp_path / "locks.db"
    replay_week1(WEEK1, db)
    published = publish_week2_rehearsal(WEEK2, db)
    assert published["week_no"] == 2
    assert published["status"] == "open"
    assert "rehearsal" in published["title"]

    conn = connect(db)
    try:
        week1 = get_week(conn, 1, 2026)
        week2 = get_week(conn, 2, 2026)
        assert week1["status"] == "locked"
        assert week2["status"] == "open"
        w1_n = int(
            conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (week1["id"],)).fetchone()["n"]
        )
        w2_n = int(
            conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (week2["id"],)).fetchone()["n"]
        )
        w2_games = int(
            conn.execute("SELECT COUNT(*) AS n FROM games WHERE week_id = ?", (week2["id"],)).fetchone()["n"]
        )
        assert w1_n == 60
        assert w2_n == 0
        assert w2_games == 5
        current = current_week(conn, 2026)
        assert current is not None
        assert current["week_no"] == 2
        assert current["status"] == "open"
    finally:
        conn.close()

    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=EASTERN)
    app = create_app(db_path=db, now_fn=lambda: now, mail_send=lambda *a, **k: "smtp")
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {mint_token('commish-sub', 'commish@example.com')}"}
        current = client.get("/api/weeks/current", headers=headers)
        assert current.status_code == 200, current.text
        body = current.json()
        assert body["week"]["week_no"] == 2
        assert body["locked"] is False
        assert body["board"] is None
        assert len(body["games"]) == 5
        five = [
            {"slot": i + 1, "game_id": body["games"][i]["id"], "market": "spread", "side": "home"}
            for i in range(5)
        ]
        saved = client.put("/api/weeks/current/picks", json={"picks": five}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert len(saved.json()["picks"]) == 5
        week1 = client.get("/api/weeks/1", headers=headers)
        assert week1.json()["week"]["status"] == "locked"
        assert sum(len(row["picks"]) for row in week1.json()["board"]) == 60
