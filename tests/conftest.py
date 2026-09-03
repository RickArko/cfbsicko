from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("WEB_AUTH_ENABLED", "true")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-cfbsicko-locks-32byt")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("REQUIRE_EMAIL_CONFIRMED", "true")
os.environ.setdefault("COMMISH_ALLOWED_EMAILS", "commish@example.com")
os.environ.setdefault("PUBLIC_APP_URL", "http://test")
os.environ["DATABASE_PATH"] = "/tmp/cfbsicko-pytest-unused.db"

from cfbsicko.config import reload_config

reload_config()

from cfbsicko.app import create_app  # noqa: E402
from cfbsicko.import_sheet import import_master_sheet  # noqa: E402
from cfbsicko.rules import EASTERN  # noqa: E402

XLSX = Path(__file__).resolve().parents[1] / "data" / "assets" / "CFB Locks MASTER SHEET 2026.xlsx"
SECRET = "test-secret-cfbsicko-locks-32byt"


def mint_token(sub: str, email: str, *, confirmed: bool = True) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + timedelta(hours=2),
        "email_verified": confirmed,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture
def clock():
    return {"now": datetime(2026, 9, 3, 17, 59, 0, tzinfo=EASTERN)}


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "locks.db"


@pytest.fixture
def imported(db_path) -> Path:
    import_master_sheet(XLSX, db_path, season=2026)
    return db_path


@pytest.fixture
def app(imported, clock):
    return create_app(db_path=imported, now_fn=lambda: clock["now"], mail_send=lambda *a, **k: "smtp")


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def commish_headers(client):
    token = mint_token("commish-sub", "commish@example.com")
    return {"Authorization": f"Bearer {token}"}


def auth_header(sub: str, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub, email)}"}


def invite(client, commish_headers, email: str, display_name: str | None = None) -> None:
    body = {"email": email}
    if display_name:
        body["display_name"] = display_name
    r = client.post("/api/admin/invites", json=body, headers=commish_headers)
    assert r.status_code == 200, r.text
