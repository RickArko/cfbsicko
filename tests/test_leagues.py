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
