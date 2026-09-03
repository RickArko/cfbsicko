from datetime import datetime

from conftest import auth_header, invite

from cfbsicko.rules import EASTERN


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_auth_config_has_no_secrets(client):
    r = client.get("/api/auth/config")
    body = r.json()
    assert "service_role" not in str(body).lower()
    assert "jwt_secret" not in str(body).lower()


def test_unauthenticated_put_401(client):
    r = client.put("/api/weeks/current/picks", json={"picks": []})
    assert r.status_code == 401


def test_non_invited_403(client):
    r = client.get("/api/weeks/current", headers=auth_header("x", "stranger@example.com"))
    assert r.status_code == 403


def test_invited_user_saves_five_not_six(client, commish_headers):
    invite(client, commish_headers, "stu@example.com", "Stu")
    headers = auth_header("stu-sub", "stu@example.com")
    week = client.get("/api/weeks/current", headers=headers).json()
    games = week["games"]
    five = [
        {"slot": 1, "game_id": games[0]["id"], "market": "spread", "side": "home"},
        {"slot": 2, "game_id": games[1]["id"], "market": "spread", "side": "away"},
        {"slot": 3, "game_id": games[2]["id"], "market": "total", "side": "over"},
        {"slot": 4, "game_id": games[3]["id"], "market": "total", "side": "under"},
        {"slot": 5, "game_id": games[4]["id"], "market": "spread", "side": "home"},
    ]
    ok = client.put("/api/weeks/current/picks", json={"picks": five}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["picks"]) == 5
    six = [*five, {"slot": 6, "game_id": games[5]["id"], "market": "spread", "side": "home"}]
    bad = client.put("/api/weeks/current/picks", json={"picks": six}, headers=headers)
    assert bad.status_code in {400, 422}


def test_picks_hidden_until_lock(client, commish_headers, clock):
    invite(client, commish_headers, "stu@example.com", "Stu")
    invite(client, commish_headers, "jack@example.com", "Jack")
    stu = auth_header("stu-sub", "stu@example.com")
    jack = auth_header("jack-sub", "jack@example.com")
    games = client.get("/api/weeks/current", headers=stu).json()["games"]
    five = [{"slot": i + 1, "game_id": games[i]["id"], "market": "spread", "side": "home"} for i in range(5)]
    assert client.put("/api/weeks/current/picks", json={"picks": five}, headers=stu).status_code == 200
    pre = client.get("/api/weeks/current", headers=jack).json()
    assert pre["board"] is None
    hidden = client.get("/api/weeks/1/board", headers=jack)
    assert hidden.status_code == 403

    clock["now"] = datetime(2026, 9, 3, 18, 0, 0, tzinfo=EASTERN)
    locked = client.put("/api/weeks/current/picks", json={"picks": five}, headers=stu)
    assert locked.status_code == 409
    board = client.get("/api/weeks/1/board", headers=jack)
    assert board.status_code == 200
    names = {row["display_name"] for row in board.json()["board"]}
    assert "Stu" in names
    stu_row = next(row for row in board.json()["board"] if row["display_name"] == "Stu")
    assert len(stu_row["picks"]) == 5


def test_lock_minus_one_second_allows_write(client, commish_headers, clock):
    invite(client, commish_headers, "mike@example.com", "Mike")
    headers = auth_header("mike-sub", "mike@example.com")
    games = client.get("/api/weeks/current", headers=headers).json()["games"]
    five = [
        {"slot": i + 1, "game_id": games[i + 10]["id"], "market": "total", "side": "over"} for i in range(5)
    ]
    clock["now"] = datetime(2026, 9, 3, 17, 59, 59, tzinfo=EASTERN)
    assert client.put("/api/weeks/current/picks", json={"picks": five}, headers=headers).status_code == 200
    clock["now"] = datetime(2026, 9, 3, 18, 0, 0, tzinfo=EASTERN)
    assert client.put("/api/weeks/current/picks", json={"picks": five}, headers=headers).status_code == 409


def test_grade_override_standings_and_snapshot(client, commish_headers):
    invite(client, commish_headers, "stu@example.com", "Stu")
    headers = auth_header("stu-sub", "stu@example.com")
    me = client.get("/api/me", headers=headers).json()
    assert me["display_name"] == "Stu"
    week = client.get("/api/weeks/current", headers=headers).json()
    pick = week["my_picks"][0]
    game_id = pick["game_id"]
    # Force a known cover: huge home win if they took home, else away.
    home_score, away_score = (70, 0) if pick["side"] in {"home", "over"} else (0, 70)
    scored = client.put(
        f"/api/admin/games/{game_id}/result",
        json={"home_score": home_score, "away_score": away_score},
        headers=commish_headers,
    )
    assert scored.status_code == 200, scored.text
    graded = client.post("/api/admin/weeks/1/grade", headers=commish_headers)
    assert graded.status_code == 200
    standings = client.get("/api/standings", headers=headers).json()
    stu = next(row for row in standings["table"] if row["display_name"] == "Stu")
    assert stu["wins"] + stu["ties"] + stu["losses"] >= 1
    assert standings["payout"]["pot"] == 0  # nobody marked paid

    paid = client.patch(
        f"/api/admin/users/{me['id']}",
        json={"buy_in_paid": True},
        headers=commish_headers,
    )
    assert paid.status_code == 200
    pot = client.get("/api/standings", headers=headers).json()["payout"]
    assert pot["pot"] == 75

    pick_id = pick["id"]
    over = client.post(
        f"/api/admin/picks/{pick_id}/override",
        json={"result": "L"},
        headers=commish_headers,
    )
    assert over.status_code == 200
    assert over.json()["result"] == "L"
    snaps = client.get("/api/admin/snapshots", headers=commish_headers).json()["snapshots"]
    assert any(s["kind"] == "grade" for s in snaps)
    body = client.get(f"/api/admin/snapshots/{snaps[0]['id']}", headers=commish_headers)
    assert body.status_code == 200
    assert "picks" in body.json()


def test_invite_sends_welcome_mail(client, commish_headers, app):
    sent: list[tuple] = []

    def capture(to, subject, body):
        sent.append((to, subject, body))
        return "smtp"

    app.state.mail_send = capture
    r = client.post(
        "/api/admin/invites",
        json={"email": "stu@example.com", "display_name": "Stu"},
        headers=commish_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["mailed"] is True
    assert len(sent) == 1
    assert sent[0][0] == "stu@example.com"
    assert "You're in" in sent[0][1]
    assert "Lock your five" in sent[0][2]


def test_publish_slate_and_mail(client, commish_headers, app):
    sent: list[tuple] = []

    def capture(to, subject, body):
        sent.append((to, subject, body))
        return "smtp"

    app.state.mail_send = capture
    invite(client, commish_headers, "player@example.com", "Wil")
    slate = """
Thursday
-   Colorado at Georgia Tech — Georgia Tech -6.5 | O/U 50.5
Friday
-   SMU at Florida State — Florida State -3.5 | O/U 53.5
"""
    pub = client.post(
        "/api/admin/weeks",
        json={
            "week_no": 2,
            "lock_at": "2026-09-10T18:00:00-04:00",
            "title": "Week 2",
            "slate_text": slate,
        },
        headers=commish_headers,
    )
    assert pub.status_code == 200, pub.text
    assert len(pub.json()["games"]) == 2
    mail = client.post("/api/admin/weeks/2/mail/slate", headers=commish_headers)
    assert mail.status_code == 200
    assert mail.json()["sent"] >= 1
    assert any("Week 2" in item[1] for item in sent)
