import os

from fastapi.testclient import TestClient

from cfbsicko.app import create_app
from cfbsicko.config import reload_config


def test_local_login_and_me(imported):
    app = create_app(db_path=imported)
    os.environ["TEST_PASS"] = "cfbSick"
    os.environ["TEST_EMAIL"] = "rickarko@pm.me"
    os.environ["PUBLIC_APP_URL"] = "http://127.0.0.1:8000"
    os.environ["HOST"] = "127.0.0.1"
    reload_config()
    try:
        with TestClient(app) as client:
            assert client.get("/api/auth/config").json()["local_login"] is True
            bad = client.post(
                "/api/auth/dev-login",
                json={"email": "rickarko@pm.me", "password": "nope"},
            )
            assert bad.status_code == 401
            ok = client.post(
                "/api/auth/dev-login",
                json={"email": "rickarko@pm.me", "password": "cfbSick"},
            )
            assert ok.status_code == 200, ok.text
            token = ok.json()["access_token"]
            me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200
            assert me.json()["email"] == "rickarko@pm.me"
            assert me.json()["is_commish"] is True
            assert me.json()["display_name"] == "Rick"
    finally:
        os.environ.pop("TEST_PASS", None)
        reload_config()


def test_local_login_off_on_production_url(imported):
    os.environ["TEST_PASS"] = "cfbSick"
    os.environ["PUBLIC_APP_URL"] = "https://cfbsicko.com"
    os.environ["HOST"] = "0.0.0.0"
    reload_config()
    try:
        app = create_app(db_path=imported)
        with TestClient(app) as client:
            assert client.get("/api/auth/config").json()["local_login"] is False
            denied = client.post(
                "/api/auth/dev-login",
                json={"email": "rickarko@pm.me", "password": "cfbSick"},
            )
            assert denied.status_code == 404
    finally:
        os.environ.pop("TEST_PASS", None)
        os.environ["PUBLIC_APP_URL"] = "http://test"
        reload_config()
