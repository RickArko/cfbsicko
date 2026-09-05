"""Environment configuration. Never read ~/.cfb_data/cfb.db."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


class Config:
    DATABASE_PATH: str = os.getenv("DATABASE_PATH") or "~/.cfbsicko/locks.db"
    HOST: str = os.getenv("HOST") or "127.0.0.1"
    PORT: int = int(os.getenv("PORT") or "8000")
    PUBLIC_APP_URL: str = (os.getenv("PUBLIC_APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    WEB_AUTH_ENABLED: bool = (os.getenv("WEB_AUTH_ENABLED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    REQUIRE_EMAIL_CONFIRMED: bool = (os.getenv("REQUIRE_EMAIL_CONFIRMED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    SUPABASE_URL: str = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    SUPABASE_PUBLISHABLE_KEY: str = os.getenv("SUPABASE_PUBLISHABLE_KEY") or ""
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY") or ""
    SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET") or ""
    SUPABASE_JWKS_URL: str = os.getenv("SUPABASE_JWKS_URL") or ""
    SUPABASE_JWT_AUDIENCE: str = os.getenv("SUPABASE_JWT_AUDIENCE") or "authenticated"
    SUPABASE_FETCH_USER_ON_VERIFY: bool = (
        os.getenv("SUPABASE_FETCH_USER_ON_VERIFY") or "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    COMMISH_ALLOWED_EMAILS: str = os.getenv("COMMISH_ALLOWED_EMAILS") or ""
    SMTP_HOST: str = os.getenv("SMTP_HOST") or "smtp.resend.com"
    SMTP_PORT: int = int(os.getenv("SMTP_PORT") or "465")
    SMTP_USER: str = os.getenv("SMTP_USER") or "resend"
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD") or ""
    SMTP_FROM: str = os.getenv("SMTP_FROM") or "CFB Sicko <locks@cfbsicko.com>"
    SMTP_USE_SSL: bool = (os.getenv("SMTP_USE_SSL") or "true").strip().lower() in {"1", "true", "yes", "on"}
    SMTP_USE_TLS: bool = (os.getenv("SMTP_USE_TLS") or "false").strip().lower() in {"1", "true", "yes", "on"}
    SEASON: int = int(os.getenv("CFBSICKO_SEASON") or "2026")
    TRUST_PROXY_HEADERS: bool = (os.getenv("CFBSICKO_TRUST_PROXY_HEADERS") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    TEST_EMAIL: str = (os.getenv("TEST_EMAIL") or "rickarko@pm.me").strip().lower()
    TEST_PASS: str = os.getenv("TEST_PASS") or ""
    TEST_DISPLAY_NAME: str = os.getenv("TEST_DISPLAY_NAME") or "Rick"
    ALLOW_TEST_LOGIN: bool = (os.getenv("ALLOW_TEST_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    CRON_TOKEN: str = os.getenv("CRON_TOKEN") or ""
    CFBD_API_KEY: str = os.getenv("CFBD_API_KEY") or ""

    @classmethod
    def database_path(cls) -> Path:
        raw = cls.DATABASE_PATH
        if "cfb_data" in raw.replace("\\", "/") and "cfbsicko" not in raw:
            raise RuntimeError(
                f"DATABASE_PATH={raw} looks like the fantasy warehouse. "
                "Use ~/.cfbsicko/locks.db (local) or /data/locks.db (Fly)."
            )
        return _expand(raw)

    @classmethod
    def supabase_browser_key(cls) -> str:
        return cls.SUPABASE_PUBLISHABLE_KEY or cls.SUPABASE_ANON_KEY

    @classmethod
    def commish_emails(cls) -> list[str]:
        emails = [part.strip().lower() for part in cls.COMMISH_ALLOWED_EMAILS.split(",") if part.strip()]
        if "*" in emails:
            raise RuntimeError("COMMISH_ALLOWED_EMAILS must never be *")
        if cls.local_login_enabled() and cls.TEST_EMAIL and cls.TEST_EMAIL not in emails:
            emails.append(cls.TEST_EMAIL)
        return emails

    @classmethod
    def local_login_enabled(cls) -> bool:
        """Password test user. Off on Fly unless ALLOW_TEST_LOGIN is set."""
        if not cls.TEST_PASS:
            return False
        if cls.ALLOW_TEST_LOGIN:
            return True
        public = cls.PUBLIC_APP_URL.lower()
        if "cfbsicko.com" in public or "fly.dev" in public:
            return False
        if cls.HOST in {"127.0.0.1", "localhost"}:
            return True
        return "127.0.0.1" in public or "localhost" in public


def reload_config() -> None:
    """Refresh Config from the current environment (tests)."""
    load_dotenv(override=False)
    Config.DATABASE_PATH = os.getenv("DATABASE_PATH") or "~/.cfbsicko/locks.db"
    Config.HOST = os.getenv("HOST") or "127.0.0.1"
    Config.PORT = int(os.getenv("PORT") or "8000")
    Config.PUBLIC_APP_URL = (os.getenv("PUBLIC_APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    Config.WEB_AUTH_ENABLED = (os.getenv("WEB_AUTH_ENABLED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    Config.REQUIRE_EMAIL_CONFIRMED = (os.getenv("REQUIRE_EMAIL_CONFIRMED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    Config.SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    Config.SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or ""
    Config.SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or ""
    Config.SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET") or ""
    Config.SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or ""
    Config.SUPABASE_JWT_AUDIENCE = os.getenv("SUPABASE_JWT_AUDIENCE") or "authenticated"
    Config.COMMISH_ALLOWED_EMAILS = os.getenv("COMMISH_ALLOWED_EMAILS") or ""
    Config.SMTP_HOST = os.getenv("SMTP_HOST") or "smtp.resend.com"
    Config.SMTP_PORT = int(os.getenv("SMTP_PORT") or "465")
    Config.SMTP_USER = os.getenv("SMTP_USER") or "resend"
    Config.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or ""
    Config.SMTP_FROM = os.getenv("SMTP_FROM") or "CFB Sicko <locks@cfbsicko.com>"
    Config.SEASON = int(os.getenv("CFBSICKO_SEASON") or "2026")
    Config.TEST_EMAIL = (os.getenv("TEST_EMAIL") or "rickarko@pm.me").strip().lower()
    Config.TEST_PASS = os.getenv("TEST_PASS") or ""
    Config.TEST_DISPLAY_NAME = os.getenv("TEST_DISPLAY_NAME") or "Rick"
    Config.ALLOW_TEST_LOGIN = (os.getenv("ALLOW_TEST_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    Config.CRON_TOKEN = os.getenv("CRON_TOKEN") or ""
    Config.CFBD_API_KEY = os.getenv("CFBD_API_KEY") or ""
