from datetime import timedelta

from conftest import auth_header, invite
from fastapi.testclient import TestClient

from cfbsicko.app import create_app
from cfbsicko.db import SCHEMA_SQL, connect
from cfbsicko.feed import FeedGame, StaticFeed
from cfbsicko.jobs import tick_odds, tick_outbox

SLATE5 = """
Thursday
-   Houston at Oklahoma — Houston -20.5 | O/U 54.5
-   Purdue at Indiana State — Purdue -14.5 | O/U 57.5
Friday
-   SMU at Florida State — Florida State -3.5 | O/U 53.5
-   Colorado at Georgia Tech — Georgia Tech -6.5 | O/U 50.5
Saturday
-   Washington State at Ole Miss — Ole Miss -23.5 | O/U 61.5
"""

FEED5 = [
    FeedGame("Houston", "Oklahoma", -20.5, 54.5, "hou-okl", day_label="Thursday"),
    FeedGame("Purdue", "Indiana State", -14.5, 57.5, "pur-isu", day_label="Thursday"),
    FeedGame("SMU", "Florida State", -3.5, 53.5, "smu-fsu", day_label="Friday"),
    FeedGame("Colorado", "Georgia Tech", -6.5, 50.5, "col-gt", day_label="Friday"),
    FeedGame("Washington State", "Ole Miss", -23.5, 61.5, "wsu-om", day_label="Saturday"),
]


def _live_app(imported, clock, feed=None, sent=None):
    captured = sent if sent is not None else []

    def capture(to, subject, body, html=None):
        captured.append((to, subject, body, html))
        return "smtp"

    app = create_app(
        db_path=imported,
        now_fn=lambda: clock["now"],
        mail_send=capture,
        feed=feed or StaticFeed(FEED5),
        cron_token="tick",
        live_ticks=False,
    )
    return app, captured


def _five(games):
    return [
        {"slot": 1, "game_id": games[0]["id"], "market": "spread", "side": "away"},
        {"slot": 2, "game_id": games[1]["id"], "market": "spread", "side": "home"},
        {"slot": 3, "game_id": games[2]["id"], "market": "total", "side": "over"},
        {"slot": 4, "game_id": games[3]["id"], "market": "total", "side": "under"},
        {"slot": 5, "game_id": games[4]["id"], "market": "spread", "side": "away"},
    ]


def test_migrate_live_does_not_change_schema_checksum(imported):
    first = connect(imported)
    cols = {row[1] for row in first.execute("PRAGMA table_info(games)")}
    first.close()
    assert "market_spread_home" in cols
    again = connect(imported)
    tables = {row[0] for row in again.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    again.close()
    assert "mail_outbox" in tables
    assert "CREATE TABLE IF NOT EXISTS invites" in SCHEMA_SQL
    assert "mail_outbox" not in SCHEMA_SQL


def test_publish_week_with_picks_is_conflict(client, commish_headers):
    r = client.post(
        "/api/admin/weeks",
        json={
            "week_no": 1,
            "lock_at": "2026-09-03T18:00:00-04:00",
            "slate_text": SLATE5,
        },
        headers=commish_headers,
    )
    assert r.status_code == 409, r.text


def test_lock_warning_one_hour_dedupes(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=10, hour=16, minute=59)
    lock_at = "2026-09-10T18:00:00-04:00"
    app, sent = _live_app(imported, clock)
    with TestClient(app) as client:
        invite(client, commish_headers, "late@example.com", "Late")
        invite(client, commish_headers, "done@example.com", "Done")
        pub = client.post(
            "/api/admin/weeks",
            json={"week_no": 2, "lock_at": lock_at, "slate_text": SLATE5, "title": "Week 2"},
            headers=commish_headers,
        )
        assert pub.status_code == 200, pub.text
        games = pub.json()["games"]
        done = auth_header("done-sub", "done@example.com")
        client.get("/api/me", headers=auth_header("late-sub", "late@example.com"))
        saved = client.put("/api/weeks/2/picks", json={"picks": _five(games)}, headers=done)
        assert saved.status_code == 200, saved.text
        clock["now"] = clock["now"] + timedelta(minutes=2)
        first = client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        assert first.status_code == 200, first.text
        second = client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        assert second.status_code == 200
    incomplete = [row for row in sent if row[0] == "late@example.com" and "1 hour" in row[1]]
    complete = [row for row in sent if row[0] == "done@example.com" and "closes in 1 hour" in row[1]]
    assert len(incomplete) == 1
    assert len(complete) == 1
    assert incomplete[0][3]
    assert "Lock your five" in incomplete[0][2]


def test_internal_tick_404_without_token(imported, clock):
    app, _ = _live_app(imported, clock)
    app.state.cron_token = ""
    with TestClient(app) as client:
        assert client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"}).status_code == 404


def test_second_save_emails_only_that_player(imported, clock, commish_headers):
    app, sent = _live_app(imported, clock)
    with TestClient(app) as client:
        invite(client, commish_headers, "both@example.com", "Both")
        invite(client, commish_headers, "other@example.com", "Other")
        pub = client.post(
            "/api/admin/weeks",
            json={
                "week_no": 2,
                "lock_at": "2026-09-10T18:00:00-04:00",
                "slate_text": SLATE5,
            },
            headers=commish_headers,
        )
        games = pub.json()["games"]
        owner = auth_header("both-sub", "both@example.com")
        other = auth_header("other-sub", "other@example.com")
        first = client.put("/api/weeks/2/picks", json={"picks": _five(games)}, headers=owner)
        assert first.status_code == 200, first.text
        sent.clear()
        five = _five(games)
        five[0]["side"] = "home"
        second = client.put("/api/weeks/2/picks", json={"picks": five}, headers=owner)
        assert second.status_code == 200, second.text
        from cfbsicko.jobs import tick_outbox

        tick_outbox(app.state.conn, clock["now"], app.state.mail_send)
        notes = client.get("/api/me/notifications", headers=owner)
        other_notes = client.get("/api/me/notifications", headers=other)
        board = client.get("/api/weeks/2", headers=owner)
    assert any(row[0] == "both@example.com" and "lineup updated" in row[1] for row in sent)
    assert not any(row[0] == "other@example.com" for row in sent)
    assert notes.json()["unread"] >= 1
    assert other_notes.json()["unread"] == 0
    assert board.json()["board"] is None


def test_partial_grade_then_finals(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=10, hour=12)
    feed = StaticFeed(
        [
            FeedGame(
                "Houston", "Oklahoma", -20.5, 54.5, "hou-okl", home_score=14, away_score=42, status="final"
            ),
            FeedGame(
                "Purdue",
                "Indiana State",
                -14.5,
                57.5,
                "pur-isu",
                home_score=21,
                away_score=10,
                status="final",
            ),
            FeedGame("SMU", "Florida State", -3.5, 53.5, "smu-fsu", status="scheduled"),
            FeedGame("Colorado", "Georgia Tech", -6.5, 50.5, "col-gt", status="scheduled"),
            FeedGame("Washington State", "Ole Miss", -23.5, 61.5, "wsu-om", status="scheduled"),
        ]
    )
    app, _sent = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        invite(client, commish_headers, "grader@example.com", "Grader")
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        frozen = client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        assert frozen.status_code == 200, frozen.text
        games = frozen.json()["games"]
        headers = auth_header("grader-sub", "grader@example.com")
        saved = client.put("/api/weeks/2/picks", json={"picks": _five(games)}, headers=headers)
        assert saved.status_code == 200, saved.text
        clock["now"] = clock["now"].replace(day=11, hour=20)
        client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        mid = client.get("/api/weeks/2", headers=headers).json()
        assert mid["week"]["status"] != "graded"
        results = {p["game_id"]: p["result"] for p in mid["my_picks"]}
        assert results[games[0]["id"]] in {"W", "T", "L"}
        assert results[games[2]["id"]] == "pending"
        feed.games = [
            FeedGame(
                "Houston", "Oklahoma", -20.5, 54.5, "hou-okl", home_score=14, away_score=42, status="final"
            ),
            FeedGame(
                "Purdue",
                "Indiana State",
                -14.5,
                57.5,
                "pur-isu",
                home_score=21,
                away_score=10,
                status="final",
            ),
            FeedGame(
                "SMU", "Florida State", -3.5, 53.5, "smu-fsu", home_score=24, away_score=20, status="final"
            ),
            FeedGame(
                "Colorado", "Georgia Tech", -6.5, 50.5, "col-gt", home_score=31, away_score=17, status="final"
            ),
            FeedGame(
                "Washington State",
                "Ole Miss",
                -23.5,
                61.5,
                "wsu-om",
                home_score=45,
                away_score=10,
                status="final",
            ),
        ]
        client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        done = client.get("/api/weeks/2", headers=headers).json()
        assert done["week"]["status"] == "graded"
        assert all(p["result"] in {"W", "T", "L"} for p in done["my_picks"])


def test_line_moved_only_holders_pre_lock(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=8, hour=12)
    feed = StaticFeed(list(FEED5))
    app, sent = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        invite(client, commish_headers, "holder@example.com", "Holder")
        invite(client, commish_headers, "otherp@example.com", "Otherp")
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        frozen = client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        games = frozen.json()["games"]
        holder = auth_header("holder-sub", "holder@example.com")
        other = auth_header("otherp-sub", "otherp@example.com")
        five = _five(games)
        put_h = client.put("/api/weeks/2/picks", json={"picks": five}, headers=holder)
        assert put_h.status_code == 200, put_h.text
        other_five = _five(games)
        other_five[0] = {"slot": 1, "game_id": games[1]["id"], "market": "total", "side": "over"}
        put_o = client.put("/api/weeks/2/picks", json={"picks": other_five}, headers=other)
        assert put_o.status_code == 200, put_o.text
        sent.clear()
        feed.games[0] = FeedGame("Houston", "Oklahoma", -22.0, 54.5, "hou-okl", day_label="Thursday")
        moved = tick_odds(app.state.conn, clock["now"], feed)
        assert moved >= 1
        tick_outbox(app.state.conn, clock["now"], app.state.mail_send)
        holders = [row for row in sent if row[0] == "holder@example.com" and "Line moved" in row[1]]
        others = [row for row in sent if row[0] == "otherp@example.com" and "Line moved" in row[1]]
        assert len(holders) == 1
        assert "Houston is now +22" in holders[0][2]
        assert "+20.5" in holders[0][2]
        assert others == []
        clock["now"] = clock["now"].replace(day=10, hour=19)
        sent.clear()
        feed.games[0] = FeedGame("Houston", "Oklahoma", -24.0, 54.5, "hou-okl", day_label="Thursday")
        tick_odds(app.state.conn, clock["now"], feed)
        tick_outbox(app.state.conn, clock["now"], app.state.mail_send)
        assert not any("Line moved" in row[1] for row in sent)


def test_ingest_refuses_week_with_picks(imported, clock, commish_headers):
    app, _ = _live_app(imported, clock)
    with TestClient(app) as client:
        before = client.get("/api/weeks/1", headers=commish_headers).json()["week"]["lock_at"]
        r = client.post(
            "/api/admin/weeks/1/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        assert r.status_code == 409, r.text
        after = client.get("/api/weeks/1", headers=commish_headers).json()["week"]["lock_at"]
        assert after == before


def test_empty_cron_is_404(client):
    r = client.post("/api/internal/tick", headers={"X-Cron-Token": "x"})
    assert r.status_code == 404


def test_migrate_live_adds_locked_at_to_existing_outbox(tmp_path):
    import sqlite3

    from cfbsicko.live import migrate_live

    conn = sqlite3.connect(tmp_path / "legacy-outbox.db")
    conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY);
        CREATE TABLE game_results (game_id INTEGER PRIMARY KEY);
        CREATE TABLE scheduled_jobs (
            id INTEGER PRIMARY KEY,
            week_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            run_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            locked_at TEXT,
            last_error TEXT
        );
        CREATE TABLE mail_outbox (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            week_id INTEGER,
            league_id INTEGER,
            to_email TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            send_after TEXT NOT NULL,
            sent_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        """
    )
    migrate_live(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mail_outbox)")}
    conn.close()
    assert "locked_at" in cols


def test_tick_jobs_claims_once_across_connections(imported, clock):
    import threading

    from cfbsicko.db import connect
    from cfbsicko.jobs import tick_jobs

    setup = connect(imported)
    week_id = int(setup.execute("SELECT id FROM weeks ORDER BY id LIMIT 1").fetchone()[0])
    setup.execute("DELETE FROM scheduled_jobs")
    setup.execute(
        "INSERT INTO scheduled_jobs (week_id, kind, run_at, status) VALUES (?, 'lock_snapshot', ?, 'pending')",
        (week_id, (clock["now"] - timedelta(minutes=1)).isoformat()),
    )
    setup.commit()
    setup.close()

    ran = [0, 0]
    barrier = threading.Barrier(2)

    def worker(idx: int) -> None:
        conn = connect(imported)
        barrier.wait()
        ran[idx] = tick_jobs(conn, clock["now"])
        conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(ran) == 1
    check = connect(imported)
    row = check.execute(
        "SELECT attempts, status FROM scheduled_jobs WHERE week_id = ? AND kind = 'lock_snapshot'",
        (week_id,),
    ).fetchone()
    check.close()
    assert row["attempts"] == 1
    assert row["status"] == "done"


def test_tick_outbox_claim_blocks_reentrant_send(imported, clock):
    import json

    from cfbsicko.db import connect
    from cfbsicko.jobs import tick_outbox

    conn = connect(imported)
    week_id = int(conn.execute("SELECT id FROM weeks ORDER BY id LIMIT 1").fetchone()[0])
    payload = json.dumps({"subject": "probe", "body": "once"})
    conn.execute(
        """
        INSERT INTO mail_outbox (kind, week_id, to_email, payload_json, dedupe_key, send_after)
        VALUES ('review_probe', ?, 'probe@example.com', ?, 'probe', ?)
        """,
        (week_id, payload, (clock["now"] - timedelta(minutes=1)).isoformat()),
    )
    conn.commit()
    sent: list[str] = []

    def send(to, subject, body, html=None):
        sent.append(to)
        assert tick_outbox(conn, clock["now"], send) == 0
        return "smtp"

    assert tick_outbox(conn, clock["now"], send) == 1
    assert sent == ["probe@example.com"]
    row = conn.execute("SELECT sent_at, locked_at FROM mail_outbox WHERE dedupe_key = 'probe'").fetchone()
    conn.close()
    assert row["sent_at"] is not None
    assert row["locked_at"] is None


def test_pick_line_keeps_plus_on_dog():
    from cfbsicko.mail import lineup_saved_body

    away_dog = [
        {
            "market": "spread",
            "side": "away",
            "away": "SMU",
            "home": "Florida State",
            "spread_home": -3.5,
        }
    ]
    _, body = lineup_saved_body(week_title="Week 2", before=[], after=away_dog, app_url="http://t")
    assert "SMU +3.5" in body

    home_dog = [
        {
            "market": "spread",
            "side": "home",
            "away": "Houston",
            "home": "Oklahoma",
            "spread_home": 3.5,
        }
    ]
    _, home_body = lineup_saved_body(week_title="Week 2", before=[], after=home_dog, app_url="http://t")
    assert "Oklahoma +3.5" in home_body

    favorite = [
        {
            "market": "spread",
            "side": "home",
            "away": "SMU",
            "home": "Florida State",
            "spread_home": -3.5,
        }
    ]
    _, fav_body = lineup_saved_body(week_title="Week 2", before=[], after=favorite, app_url="http://t")
    assert "Florida State -3.5" in fav_body


def test_migrate_live_adds_nullable_updated_at(tmp_path):
    import sqlite3

    from cfbsicko.live import migrate_live

    conn = sqlite3.connect(tmp_path / "legacy-results.db")
    conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY);
        CREATE TABLE game_results (
            game_id INTEGER PRIMARY KEY,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual'
        );
        INSERT INTO game_results (game_id, home_score, away_score, source) VALUES (1, 21, 17, 'manual');
        """
    )
    migrate_live(conn)
    cols = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(game_results)")}
    conn.close()
    assert "updated_at" in cols
    assert cols["updated_at"] is None


def test_connect_does_not_clear_fresh_outbox_lock(imported, clock):
    import json

    from cfbsicko.db import connect

    setup = connect(imported)
    week_id = int(setup.execute("SELECT id FROM weeks ORDER BY id LIMIT 1").fetchone()[0])
    setup.execute(
        """
        INSERT INTO mail_outbox (kind, week_id, to_email, payload_json, dedupe_key, send_after, locked_at)
        VALUES ('held', ?, 'held@example.com', ?, 'held', ?, ?)
        """,
        (
            week_id,
            json.dumps({"subject": "s", "body": "b"}),
            (clock["now"] - timedelta(minutes=1)).isoformat(),
            clock["now"].isoformat(),
        ),
    )
    setup.commit()
    setup.close()
    again = connect(imported)
    row = again.execute("SELECT locked_at FROM mail_outbox WHERE dedupe_key = 'held'").fetchone()
    again.close()
    assert row["locked_at"] is not None


def test_stale_outbox_lock_is_reclaimed(imported, clock):
    import json

    from cfbsicko.db import connect
    from cfbsicko.jobs import tick_outbox

    conn = connect(imported)
    week_id = int(conn.execute("SELECT id FROM weeks ORDER BY id LIMIT 1").fetchone()[0])
    conn.execute(
        """
        INSERT INTO mail_outbox (kind, week_id, to_email, payload_json, dedupe_key, send_after, locked_at)
        VALUES ('stale', ?, 'stale@example.com', ?, 'stale', ?, ?)
        """,
        (
            week_id,
            json.dumps({"subject": "s", "body": "b"}),
            (clock["now"] - timedelta(minutes=1)).isoformat(),
            (clock["now"] - timedelta(minutes=5)).isoformat(),
        ),
    )
    conn.commit()
    sent: list[str] = []

    def send(to, subject, body, html=None):
        sent.append(to)
        return "smtp"

    assert tick_outbox(conn, clock["now"], send) == 1
    assert sent == ["stale@example.com"]
    conn.close()


def test_outbox_due_filter_does_not_starve_new_mail(imported, clock):
    import json

    from cfbsicko.db import connect
    from cfbsicko.jobs import tick_outbox

    conn = connect(imported)
    week_id = int(conn.execute("SELECT id FROM weeks ORDER BY id LIMIT 1").fetchone()[0])
    payload = json.dumps({"subject": "s", "body": "b"})
    future = (clock["now"] + timedelta(hours=1)).isoformat()
    due = (clock["now"] - timedelta(minutes=1)).isoformat()
    for i in range(20):
        conn.execute(
            """
            INSERT INTO mail_outbox (kind, week_id, to_email, payload_json, dedupe_key, send_after)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"retry{i}", week_id, f"r{i}@example.com", payload, f"retry{i}", future),
        )
    conn.execute(
        """
        INSERT INTO mail_outbox (kind, week_id, to_email, payload_json, dedupe_key, send_after)
        VALUES ('due', ?, 'due@example.com', ?, 'due', ?)
        """,
        (week_id, payload, due),
    )
    conn.commit()
    sent: list[str] = []

    def send(to, subject, body, html=None):
        sent.append(to)
        return "smtp"

    assert tick_outbox(conn, clock["now"], send) == 1
    assert sent == ["due@example.com"]
    conn.close()


def test_partial_grade_skips_in_progress(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=10, hour=12)
    feed = StaticFeed(
        [
            FeedGame(
                "Houston",
                "Oklahoma",
                -20.5,
                54.5,
                "hou-okl",
                home_score=7,
                away_score=14,
                status="in_progress",
            ),
            FeedGame("Purdue", "Indiana State", -14.5, 57.5, "pur-isu"),
            FeedGame("SMU", "Florida State", -3.5, 53.5, "smu-fsu"),
            FeedGame("Colorado", "Georgia Tech", -6.5, 50.5, "col-gt"),
            FeedGame("Washington State", "Ole Miss", -23.5, 61.5, "wsu-om"),
        ]
    )
    app, _ = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        invite(client, commish_headers, "live@example.com", "Live")
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        frozen = client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        games = frozen.json()["games"]
        headers = auth_header("live-sub", "live@example.com")
        saved = client.put("/api/weeks/2/picks", json={"picks": _five(games)}, headers=headers)
        assert saved.status_code == 200, saved.text
        clock["now"] = clock["now"].replace(day=11, hour=20)
        client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        mid = client.get("/api/weeks/2", headers=headers).json()
    assert mid["my_picks"][0]["result"] == "pending"


def test_manual_score_is_not_overwritten_by_feed(imported, clock, commish_headers):
    from cfbsicko.jobs import tick_scores
    from cfbsicko.store import set_game_result

    clock["now"] = clock["now"].replace(year=2026, month=9, day=11, hour=20)
    feed = StaticFeed(
        [
            FeedGame(
                "Houston",
                "Oklahoma",
                -20.5,
                54.5,
                "hou-okl",
                home_score=99,
                away_score=0,
                status="final",
            ),
            FeedGame("Purdue", "Indiana State", -14.5, 57.5, "pur-isu"),
            FeedGame("SMU", "Florida State", -3.5, 53.5, "smu-fsu"),
            FeedGame("Colorado", "Georgia Tech", -6.5, 50.5, "col-gt"),
            FeedGame("Washington State", "Ole Miss", -23.5, 61.5, "wsu-om"),
        ]
    )
    app, _ = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        frozen = client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        game_id = frozen.json()["games"][0]["id"]
        set_game_result(
            app.state.conn, game_id, home_score=14, away_score=42, entered_by=None, source="manual"
        )
        tick_scores(app.state.conn, clock["now"], feed)
        row = app.state.conn.execute(
            "SELECT home_score, away_score, source FROM game_results WHERE game_id = ?",
            (game_id,),
        ).fetchone()
    assert (row["home_score"], row["away_score"], row["source"]) == (14, 42, "manual")


def test_remind_missing_stays_in_active_league(imported, clock, commish_headers):
    app, sent = _live_app(imported, clock)
    with TestClient(app) as client:
        invite(client, commish_headers, "default@example.com", "Default")
        created = client.post(
            "/api/admin/leagues",
            json={"name": "Arrive 2026", "buy_in": 75},
            headers=commish_headers,
        )
        assert created.status_code == 200, created.text
        side = created.json()
        side_headers = {**commish_headers, "X-League-Id": str(side["id"])}
        invite(client, side_headers, "arrive@example.com", "Arrive")
        client.get("/api/me", headers=auth_header("default-sub", "default@example.com"))
        client.get("/api/me", headers=auth_header("arrive-sub", "arrive@example.com"))
        pub = client.post(
            "/api/admin/weeks",
            json={"week_no": 2, "lock_at": "2026-09-10T18:00:00-04:00", "slate_text": SLATE5},
            headers=commish_headers,
        )
        assert pub.status_code == 200, pub.text
        sent.clear()
        mailed = client.post("/api/admin/weeks/2/mail/reminder", headers=side_headers)
        assert mailed.status_code == 200, mailed.text
    assert any(row[0] == "arrive@example.com" for row in sent)
    assert not any(row[0] == "default@example.com" for row in sent)


def test_default_feed_wires_cfbd_when_key_present():
    from cfbsicko.feed import CfbdFeed, EmptyFeed, default_feed

    assert isinstance(default_feed(""), EmptyFeed)
    lines = [
        {
            "id": 401,
            "awayTeam": "Houston",
            "homeTeam": "Oklahoma",
            "startDate": "2026-09-10T23:00:00+00:00",
            "lines": [{"provider": "consensus", "spread": -20.5, "overUnder": 54.5}],
        }
    ]

    def get_json(path, params):
        return lines if path == "/lines" else []

    feed = CfbdFeed("k", get_json=get_json)
    slate = feed.slate(2026, 2)
    assert slate[0].away == "Houston"
    assert slate[0].spread_home == -20.5
    assert slate[0].provider_game_id == "401"


def _final_feed():
    return StaticFeed(
        [
            FeedGame(
                "Houston", "Oklahoma", -20.5, 54.5, "hou-okl", home_score=14, away_score=42, status="final"
            ),
            FeedGame(
                "Purdue",
                "Indiana State",
                -14.5,
                57.5,
                "pur-isu",
                home_score=21,
                away_score=10,
                status="final",
            ),
            FeedGame(
                "SMU", "Florida State", -3.5, 53.5, "smu-fsu", home_score=24, away_score=20, status="final"
            ),
            FeedGame(
                "Colorado", "Georgia Tech", -6.5, 50.5, "col-gt", home_score=31, away_score=17, status="final"
            ),
            FeedGame(
                "Washington State",
                "Ole Miss",
                -23.5,
                61.5,
                "wsu-om",
                home_score=45,
                away_score=10,
                status="final",
            ),
        ]
    )


def test_tick_scores_fetches_corrections_for_graded_week(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=10, hour=12)
    feed = _final_feed()
    app, _ = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        invite(client, commish_headers, "grader@example.com", "Grader")
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        frozen = client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        games = frozen.json()["games"]
        headers = auth_header("grader-sub", "grader@example.com")
        saved = client.put("/api/weeks/2/picks", json={"picks": _five(games)}, headers=headers)
        assert saved.status_code == 200, saved.text
        clock["now"] = clock["now"].replace(day=11, hour=20)
        client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        done = client.get("/api/weeks/2", headers=headers).json()
        assert done["week"]["status"] == "graded", done
        feed.games[0] = FeedGame(
            "Houston", "Oklahoma", -20.5, 54.5, "hou-okl", home_score=14, away_score=45, status="final"
        )
        client.post("/api/internal/tick", headers={"X-Cron-Token": "tick"})
        row = app.state.conn.execute(
            "SELECT away_score FROM game_results WHERE game_id = ?", (games[0]["id"],)
        ).fetchone()
    assert row["away_score"] == 45


def test_publish_resolves_provider_ids(imported, clock, commish_headers):
    app, _ = _live_app(imported, clock)
    with TestClient(app) as client:
        pub = client.post(
            "/api/admin/weeks",
            json={"week_no": 2, "lock_at": "2026-09-10T18:00:00-04:00", "slate_text": SLATE5},
            headers=commish_headers,
        )
        assert pub.status_code == 200, pub.text
        body = pub.json()
        assert body["provider_matched"] == 5
        assert all(game["provider_game_id"] for game in body["games"])


def test_admin_live_reports_newest_draft_week(imported, clock, commish_headers):
    feed = StaticFeed(list(FEED5))
    app, _ = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        ingested = client.post(
            "/api/admin/weeks/3/ingest",
            json={"lock_at": "2026-09-17T18:00:00-04:00"},
            headers=commish_headers,
        )
        assert ingested.status_code == 200, ingested.text
        game = ingested.json()["games"][0]
        cleared = client.patch(
            f"/api/admin/games/{game['id']}/provider",
            json={"provider_game_id": ""},
            headers=commish_headers,
        )
        assert cleared.status_code == 200, cleared.text
        live = client.get("/api/admin/live", headers=commish_headers).json()
        assert live["week"]["week_no"] == 3
        unmatched = {(row["away"], row["home"]) for row in live["unmatched"]}
        assert (game["away"], game["home"]) in unmatched


def test_bad_lock_at_is_rejected_before_any_mutation(imported, clock, commish_headers):
    app, _ = _live_app(imported, clock)
    with TestClient(app) as client:
        before = client.get("/api/weeks/1", headers=commish_headers).json()["week"]
        picks_before = app.state.conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
        weeks_before = app.state.conn.execute("SELECT COUNT(*) FROM weeks").fetchone()[0]

        bad_ingest = client.post(
            "/api/admin/weeks/1/ingest",
            json={"lock_at": "not-a-date", "force": True},
            headers=commish_headers,
        )
        assert bad_ingest.status_code == 400, bad_ingest.text
        bad_publish = client.post(
            "/api/admin/weeks",
            json={"week_no": 9, "lock_at": "garbage", "slate_text": SLATE5, "force": True},
            headers=commish_headers,
        )
        assert bad_publish.status_code == 400, bad_publish.text
        bad_patch = client.patch("/api/admin/weeks/1", json={"lock_at": "nope"}, headers=commish_headers)
        assert bad_patch.status_code == 400, bad_patch.text

        after = client.get("/api/weeks/1", headers=commish_headers).json()["week"]
        assert after["lock_at"] == before["lock_at"]
        assert after["title"] == before["title"]
        assert app.state.conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == picks_before
        assert app.state.conn.execute("SELECT COUNT(*) FROM weeks").fetchone()[0] == weeks_before


def test_force_replace_clears_week_records(imported, clock, commish_headers):
    app, _ = _live_app(imported, clock)
    with TestClient(app) as client:
        client.get("/api/weeks/1", headers=commish_headers)
        conn = app.state.conn
        week_id = conn.execute("SELECT id FROM weeks WHERE week_no = 1").fetchone()[0]
        user_id = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]

        def seed_record() -> None:
            conn.execute(
                "INSERT OR REPLACE INTO week_records (user_id, week_id, wins, ties, losses)"
                " VALUES (?, ?, 4, 0, 1)",
                (user_id, week_id),
            )
            conn.commit()

        seed_record()
        pub = client.post(
            "/api/admin/weeks",
            json={
                "week_no": 1,
                "lock_at": "2026-09-03T18:00:00-04:00",
                "slate_text": SLATE5,
                "force": True,
            },
            headers=commish_headers,
        )
        assert pub.status_code == 200, pub.text
        assert (
            conn.execute("SELECT COUNT(*) FROM week_records WHERE week_id = ?", (week_id,)).fetchone()[0] == 0
        )

        seed_record()
        ing = client.post(
            "/api/admin/weeks/1/ingest",
            json={"lock_at": "2026-09-03T18:00:00-04:00", "force": True},
            headers=commish_headers,
        )
        assert ing.status_code == 200, ing.text
        assert (
            conn.execute("SELECT COUNT(*) FROM week_records WHERE week_id = ?", (week_id,)).fetchone()[0] == 0
        )


def test_line_ticks_are_kept_before_lock(imported, clock, commish_headers):
    clock["now"] = clock["now"].replace(year=2026, month=9, day=8, hour=12)
    feed = StaticFeed(list(FEED5))
    app, _ = _live_app(imported, clock, feed=feed)
    with TestClient(app) as client:
        client.post(
            "/api/admin/weeks/2/ingest",
            json={"lock_at": "2026-09-10T18:00:00-04:00"},
            headers=commish_headers,
        )
        client.post("/api/admin/weeks/2/freeze", headers=commish_headers)
        feed.games[0] = FeedGame("Houston", "Oklahoma", -22.0, 54.5, "hou-okl", day_label="Thursday")
        moved = tick_odds(app.state.conn, clock["now"], feed)
        assert moved >= 1
        kept = app.state.conn.execute("SELECT COUNT(*) FROM line_ticks").fetchone()[0]
        assert kept >= 1


def test_notification_unread_count_is_not_capped_by_page(imported, clock, commish_headers):
    app, _ = _live_app(imported, clock)
    with TestClient(app) as client:
        invite(client, commish_headers, "busy@example.com", "Busy")
        me = client.get("/api/me", headers=auth_header("busy-sub", "busy@example.com")).json()
        conn = app.state.conn
        for i in range(55):
            conn.execute(
                "INSERT INTO notifications (user_id, kind, title, body, href)"
                " VALUES (?, 'note', ?, ?, '/app')",
                (me["id"], f"note {i}", "body"),
            )
        conn.commit()
        data = client.get("/api/me/notifications", headers=auth_header("busy-sub", "busy@example.com")).json()
    assert len(data["items"]) == 50
    assert data["unread"] == 55


def test_live_ticks_run_off_loop_with_dedicated_connection(imported, clock, commish_headers):
    import asyncio
    import contextlib
    import threading

    from cfbsicko import jobs as jobs_mod
    from cfbsicko.app import _run_live_ticks

    seen: dict[str, object] = {}
    ready = threading.Event()
    real_tick_jobs = jobs_mod.tick_jobs

    def spy_jobs(conn, now):
        seen["thread"] = threading.get_ident()
        seen["conn"] = conn
        ready.set()
        return 0

    jobs_mod.tick_jobs = spy_jobs
    app, _ = _live_app(imported, clock)
    try:
        with TestClient(app) as client:
            client.get("/api/weeks/1", headers=commish_headers)
            shared = app.state.conn
            assert shared is not None

            async def run() -> None:
                task = asyncio.create_task(_run_live_ticks(app))
                for _ in range(500):
                    if ready.is_set():
                        break
                    await asyncio.sleep(0.02)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            asyncio.run(run())
    finally:
        jobs_mod.tick_jobs = real_tick_jobs
    assert seen["thread"] != threading.get_ident()
    assert seen["conn"] is not shared


def test_cfbd_scoreboard_parses_nested_teams_and_keeps_flat_fallback():
    from cfbsicko.feed import CfbdFeed

    scoreboard = [
        {
            "id": 501,
            "awayTeam": {"school": "Houston", "points": 30},
            "homeTeam": {"school": "Oklahoma", "points": 31},
            "status": {"type": {"name": "in_progress", "completed": False}},
            "period": 4,
        }
    ]
    games_flat = [
        {
            "id": 502,
            "awayTeam": "Purdue",
            "homeTeam": "Indiana State",
            "awayPoints": 10,
            "homePoints": 21,
            "status": "completed",
            "completed": True,
        }
    ]

    feed = CfbdFeed("k", get_json=lambda path, params: scoreboard if path == "/scoreboard" else [])
    nested = feed.scores(["501"])
    assert nested[0].away == "Houston"
    assert nested[0].home == "Oklahoma"
    assert nested[0].away_score == 30
    assert nested[0].home_score == 31
    assert nested[0].status == "in_progress"

    fallback = CfbdFeed("k", get_json=lambda path, params: [] if path == "/scoreboard" else games_flat)
    flat = fallback.scores(["502"])
    assert flat[0].away == "Purdue"
    assert flat[0].home_score == 21
    assert flat[0].status == "final"
