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
        assert "20.5" in holders[0][2]
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
        r = client.post(
            "/api/admin/weeks/1/ingest",
            json={"lock_at": "2026-09-03T18:00:00-04:00"},
            headers=commish_headers,
        )
        assert r.status_code == 409, r.text


def test_empty_cron_is_404(client):
    r = client.post("/api/internal/tick", headers={"X-Cron-Token": "x"})
    assert r.status_code == 404
