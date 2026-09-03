from conftest import auth_header, invite


def test_me_includes_default_league(client, commish_headers):
    invite(client, commish_headers, "stu@example.com", "Stu")
    me = client.get("/api/me", headers=auth_header("stu-sub", "stu@example.com")).json()
    assert me["league"]["slug"] == "cfbsicko"
    assert any(row["slug"] == "cfbsicko" for row in me["leagues"])


def test_second_league_own_buy_in_and_roster(client, commish_headers):
    invite(client, commish_headers, "stu@example.com", "Stu")
    stu = auth_header("stu-sub", "stu@example.com")
    me = client.get("/api/me", headers=stu).json()

    created = client.post(
        "/api/admin/leagues",
        json={"name": "Side pot", "buy_in": 50, "extra_owed": 25, "bottom_n": 1},
        headers=commish_headers,
    )
    assert created.status_code == 200, created.text
    side = created.json()
    assert side["buy_in"] == 50
    assert side["slug"].startswith("side-pot")

    add = client.post(
        f"/api/admin/leagues/{side['id']}/members",
        json={"email": "stu@example.com", "display_name": "Stu"},
        headers=commish_headers,
    )
    assert add.status_code == 200, add.text

    side_headers = {**commish_headers, "X-League-Id": str(side["id"])}
    paid = client.patch(
        f"/api/admin/users/{me['id']}",
        json={"buy_in_paid": True},
        headers=side_headers,
    )
    assert paid.status_code == 200, paid.text

    default = client.get("/api/standings", headers=stu).json()
    assert default["league"]["slug"] == "cfbsicko"
    assert default["payout"]["pot"] == 0

    isolated = client.get("/api/standings", headers={**stu, "X-League-Id": str(side["id"])}).json()
    assert isolated["league"]["id"] == side["id"]
    assert isolated["payout"]["buy_in"] == 50
    assert isolated["payout"]["pot"] == 50
    names = {row["display_name"] for row in isolated["table"]}
    assert "Stu" in names
    assert names != {row["display_name"] for row in default["table"]} or len(isolated["table"]) < len(
        default["table"]
    )


def test_site_admin_email_is_commish(client, monkeypatch):
    from cfbsicko.config import reload_config

    monkeypatch.setenv(
        "COMMISH_ALLOWED_EMAILS",
        "commish@example.com,stuartmfeeley@gmail.com",
    )
    reload_config()
    try:
        headers = auth_header("stuart-sub", "stuartmfeeley@gmail.com")
        me = client.get("/api/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["is_commish"] is True
        created = client.post(
            "/api/admin/leagues",
            json={"name": "Stuarts league", "buy_in": 20},
            headers=headers,
        )
        assert created.status_code == 200, created.text
    finally:
        monkeypatch.setenv("COMMISH_ALLOWED_EMAILS", "commish@example.com")
        reload_config()


def test_patch_league_rejects_empty_name_and_zero_buy_in(client, commish_headers):
    created = client.post(
        "/api/admin/leagues",
        json={"name": "Side pot", "buy_in": 50, "bottom_n": 0},
        headers=commish_headers,
    )
    assert created.status_code == 200, created.text
    league_id = created.json()["id"]
    empty = client.patch(
        f"/api/admin/leagues/{league_id}",
        json={"name": "   "},
        headers=commish_headers,
    )
    assert empty.status_code == 400
    assert "name" in empty.json()["detail"]
    zero = client.patch(
        f"/api/admin/leagues/{league_id}",
        json={"buy_in": 0},
        headers=commish_headers,
    )
    assert zero.status_code == 400
    negative = client.patch(
        f"/api/admin/leagues/{league_id}",
        json={"bottom_n": -1},
        headers=commish_headers,
    )
    assert negative.status_code == 400
    kept = client.get("/api/me", headers=commish_headers).json()
    assert any(row["id"] == league_id and row["buy_in"] == 50 for row in kept["leagues"])


def test_stale_league_header_is_forbidden_not_invite_denied(client, commish_headers):
    invite(client, commish_headers, "stu@example.com", "Stu")
    created = client.post(
        "/api/admin/leagues",
        json={"name": "Side pot", "buy_in": 50},
        headers=commish_headers,
    )
    assert created.status_code == 200, created.text
    side = created.json()
    stu = auth_header("stu-sub", "stu@example.com")
    stale = client.get("/api/me", headers={**stu, "X-League-Id": str(side["id"])})
    assert stale.status_code == 403
    assert "member" in stale.json()["detail"].lower()
    ok = client.get("/api/me", headers=stu)
    assert ok.status_code == 200
    assert ok.json()["league"]["slug"] == "cfbsicko"


def test_invite_unknown_league_id_is_404(client, commish_headers):
    r = client.post(
        "/api/admin/invites",
        json={"email": "ghost@example.com", "display_name": "Ghost", "league_id": 99999},
        headers=commish_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "League not found"


def test_standings_mail_uses_active_league_only(client, commish_headers, app):
    sent: list[tuple] = []

    def capture(to, subject, body):
        sent.append((to, subject, body))
        return "smtp"

    app.state.mail_send = capture
    invite(client, commish_headers, "main@example.com", "MainOnly")
    assert client.get("/api/me", headers=auth_header("main-sub", "main@example.com")).status_code == 200
    created = client.post(
        "/api/admin/leagues",
        json={"name": "Side pot", "buy_in": 50, "bottom_n": 0},
        headers=commish_headers,
    )
    assert created.status_code == 200, created.text
    side = created.json()
    add = client.post(
        f"/api/admin/leagues/{side['id']}/members",
        json={"email": "side@example.com", "display_name": "SideOnly"},
        headers=commish_headers,
    )
    assert add.status_code == 200, add.text
    assert client.get("/api/me", headers=auth_header("side-sub", "side@example.com")).status_code == 200
    sent.clear()
    side_headers = {**commish_headers, "X-League-Id": str(side["id"])}
    mail = client.post("/api/admin/weeks/1/mail/standings", headers=side_headers)
    assert mail.status_code == 200, mail.text
    tos = [item[0] for item in sent]
    assert "side@example.com" in tos
    assert "main@example.com" not in tos
    bodies = "\n".join(item[2] for item in sent)
    assert "SideOnly" in bodies
    assert "MainOnly" not in bodies
