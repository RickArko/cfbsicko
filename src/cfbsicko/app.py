"""FastAPI app: health, auth config, picks, admin, standings. Serves the Vue dist."""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cfbsicko.auth import AuthenticatedUser, check_local_password, get_auth_user, mint_local_token
from cfbsicko.config import Config
from cfbsicko.db import connect
from cfbsicko.rate_limit import RateLimiter
from cfbsicko.rules import PickSpec, PickValidationError
from cfbsicko.store import (
    InviteRequiredError,
    LockClosedError,
    NotFoundError,
    board,
    create_invite,
    current_week,
    get_snapshot,
    get_week,
    grade_week,
    list_games,
    list_invited_emails,
    list_snapshots,
    list_user_picks,
    list_users,
    override_pick,
    publish_slate,
    save_picks,
    set_game_result,
    set_paid,
    standings,
    update_week,
    upsert_invited_user,
    users_missing_picks,
    week_is_writable,
    write_snapshot,
)


def _frontend_dist() -> Path | None:
    raw = os.environ.get("CFBSICKO_FRONTEND_DIST")
    candidates = [
        Path(raw) if raw else None,
        Path("/app/frontend/dist"),
        Path(__file__).resolve().parent / "static",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
    ]
    for path in candidates:
        if path is not None and (path / "index.html").is_file():
            return path
    return None


class PickIn(BaseModel):
    game_id: int
    market: str
    side: str
    slot: int = Field(ge=1, le=5)


class PicksIn(BaseModel):
    picks: list[PickIn]


class InviteIn(BaseModel):
    email: str
    display_name: str | None = None


class SlateIn(BaseModel):
    week_no: int
    slate_text: str
    lock_at: str
    title: str | None = None


class WeekPatch(BaseModel):
    lock_at: str | None = None
    status: str | None = None


class ScoreIn(BaseModel):
    home_score: int
    away_score: int


class OverrideIn(BaseModel):
    result: str


class PaidIn(BaseModel):
    buy_in_paid: bool


class LocalLoginIn(BaseModel):
    email: str
    password: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


def create_app(
    *,
    db_path: Path | None = None,
    now_fn=None,
    mail_send=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        conn = getattr(_app.state, "conn", None)
        if conn is not None:
            conn.close()

    app = FastAPI(title="CFB Sicko", version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path or Config.database_path()
    app.state.now_fn = now_fn or _now_utc
    app.state.mail_send = mail_send
    app.state.limiter = RateLimiter()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[Config.PUBLIC_APP_URL, "http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def db() -> sqlite3.Connection:
        conn = getattr(app.state, "conn", None)
        if conn is None:
            app.state.conn = connect(app.state.db_path)
        return app.state.conn

    def now() -> datetime:
        return app.state.now_fn()

    def league_user(auth: AuthenticatedUser = Depends(get_auth_user)) -> dict[str, Any]:
        try:
            return upsert_invited_user(db(), auth)
        except InviteRequiredError as exc:
            raise HTTPException(status_code=403, detail="Invite required") from exc

    def commish(user: dict[str, Any] = Depends(league_user)) -> dict[str, Any]:
        email = (user.get("email") or "").lower()
        if user.get("is_commish") or email in Config.commish_emails():
            return user
        raise HTTPException(status_code=403, detail="Commissioner only")

    @app.middleware("http")
    async def canonical_host(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        path = request.url.path
        public = Config.PUBLIC_APP_URL
        if not public.startswith("https://"):
            return await call_next(request)
        if path.startswith("/api/"):
            return await call_next(request)
        # Only www → apex. Leave *.fly.dev alone so the site works before Namecheap DNS.
        if host == "www.cfbsicko.com":
            dest = public + path
            if request.url.query:
                dest += "?" + request.url.query
            return RedirectResponse(dest, status_code=301)
        return await call_next(request)

    frontend = _frontend_dist()

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "service": "cfbsicko",
            "season": Config.SEASON,
            "frontend": frontend is not None,
        }

    @app.get("/api/auth/config")
    def auth_config():
        return {
            "supabase_url": Config.SUPABASE_URL or None,
            "supabase_anon_key": Config.supabase_browser_key() or None,
            "public_app_url": Config.PUBLIC_APP_URL,
            "auth_enabled": Config.WEB_AUTH_ENABLED,
            "local_login": Config.local_login_enabled(),
            "test_email": Config.TEST_EMAIL if Config.local_login_enabled() else None,
        }

    @app.post("/api/auth/dev-login")
    def dev_login(body: LocalLoginIn):
        if not Config.local_login_enabled():
            raise HTTPException(status_code=404, detail="Local login is off")
        if not check_local_password(body.email, body.password):
            raise HTTPException(status_code=401, detail="Bad email or password")
        email = Config.TEST_EMAIL
        create_invite(db(), email=email, display_name=Config.TEST_DISPLAY_NAME, invited_by=None)
        upsert_invited_user(
            db(),
            AuthenticatedUser(
                user_id=f"local:{email}",
                email=email,
                role="authenticated",
                email_confirmed=True,
                claims={},
            ),
        )
        return {"access_token": mint_local_token(email), "token_type": "bearer"}

    @app.get("/api/me")
    def me(user: dict[str, Any] = Depends(league_user)):
        return {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "is_commish": bool(user["is_commish"]),
            "buy_in_paid": bool(user["buy_in_paid"]),
        }

    def _week_payload(week: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        writable = week_is_writable(week, now())
        payload = {
            "week": week,
            "games": list_games(db(), week["id"]),
            "my_picks": list_user_picks(db(), user["id"], week["id"]),
            "locked": not writable,
            "board": None if writable else board(db(), week, now()),
        }
        return payload

    @app.get("/api/weeks/current")
    def week_current(user: dict[str, Any] = Depends(league_user)):
        week = current_week(db())
        if week is None:
            raise HTTPException(status_code=404, detail="No week published")
        return _week_payload(week, user)

    @app.get("/api/weeks/{week_no}")
    def week_get(week_no: int, user: dict[str, Any] = Depends(league_user)):
        try:
            week = get_week(db(), week_no)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Week not found") from exc
        return _week_payload(week, user)

    @app.put("/api/weeks/current/picks")
    def put_current_picks(body: PicksIn, user: dict[str, Any] = Depends(league_user)):
        week = current_week(db())
        if week is None:
            raise HTTPException(status_code=404, detail="No week published")
        return _put_picks(week, body, user)

    @app.put("/api/weeks/{week_no}/picks")
    def put_week_picks(week_no: int, body: PicksIn, user: dict[str, Any] = Depends(league_user)):
        try:
            week = get_week(db(), week_no)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Week not found") from exc
        return _put_picks(week, body, user)

    def _put_picks(week: dict[str, Any], body: PicksIn, user: dict[str, Any]) -> dict[str, Any]:
        key = f"picks:{user['id']}"
        if not app.state.limiter.allow(key):
            raise HTTPException(status_code=429, detail="Too many pick writes")
        specs = [PickSpec(game_id=p.game_id, market=p.market, side=p.side, slot=p.slot) for p in body.picks]
        try:
            saved = save_picks(db(), user_id=user["id"], week=week, picks=specs, now=now())
        except LockClosedError as exc:
            raise HTTPException(status_code=409, detail="Week is locked") from exc
        except PickValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"picks": saved}

    @app.get("/api/weeks/{week_no}/board")
    def week_board(week_no: int, user: dict[str, Any] = Depends(league_user)):
        week = get_week(db(), week_no)
        revealed = board(db(), week, now())
        if revealed is None:
            raise HTTPException(status_code=403, detail="Board is hidden until lock")
        return {"week": week, "board": revealed}

    @app.get("/api/standings")
    def get_standings(user: dict[str, Any] = Depends(league_user)):
        return standings(db())

    @app.post("/api/admin/invites")
    def admin_invite(body: InviteIn, user: dict[str, Any] = Depends(commish)):
        invite = create_invite(db(), email=body.email, display_name=body.display_name, invited_by=user["id"])
        return {"email": invite["email"], "display_name": invite["display_name"], "id": invite["id"]}

    @app.post("/api/admin/weeks")
    def admin_publish(body: SlateIn, user: dict[str, Any] = Depends(commish)):
        try:
            week = publish_slate(
                db(),
                week_no=body.week_no,
                slate_text=body.slate_text,
                lock_at=body.lock_at,
                title=body.title,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"week": week, "games": list_games(db(), week["id"])}

    @app.patch("/api/admin/weeks/{week_no}")
    def admin_week_patch(week_no: int, body: WeekPatch, user: dict[str, Any] = Depends(commish)):
        try:
            week = update_week(db(), week_no, lock_at=body.lock_at, status=body.status)
        except (NotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.status == "locked":
            write_snapshot(db(), week["id"], "lock")
        return {"week": week}

    @app.put("/api/admin/games/{game_id}/result")
    def admin_score(game_id: int, body: ScoreIn, user: dict[str, Any] = Depends(commish)):
        return set_game_result(
            db(),
            game_id,
            home_score=body.home_score,
            away_score=body.away_score,
            entered_by=user["id"],
        )

    @app.post("/api/admin/weeks/{week_no}/grade")
    def admin_grade(week_no: int, user: dict[str, Any] = Depends(commish)):
        return grade_week(db(), week_no)

    @app.post("/api/admin/picks/{pick_id}/override")
    def admin_override(pick_id: int, body: OverrideIn, user: dict[str, Any] = Depends(commish)):
        try:
            return override_pick(db(), pick_id, body.result, actor_user_id=user["id"])
        except (NotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/admin/users/{user_id}")
    def admin_user(user_id: int, body: PaidIn, user: dict[str, Any] = Depends(commish)):
        try:
            return set_paid(db(), user_id, body.buy_in_paid)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc

    @app.get("/api/admin/users")
    def admin_users(user: dict[str, Any] = Depends(commish)):
        return {"users": list_users(db())}

    @app.get("/api/admin/snapshots")
    def admin_snapshots(user: dict[str, Any] = Depends(commish)):
        return {"snapshots": list_snapshots(db())}

    @app.get("/api/admin/snapshots/{snapshot_id}")
    def admin_snapshot(snapshot_id: int, user: dict[str, Any] = Depends(commish)):
        try:
            return get_snapshot(db(), snapshot_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Snapshot not found") from exc

    def _mail(to: str, subject: str, body: str) -> str:
        if app.state.mail_send is not None:
            return app.state.mail_send(to, subject, body)
        from cfbsicko.mail import send_mail

        return send_mail(to, subject, body)

    @app.post("/api/admin/weeks/{week_no}/mail/slate")
    def admin_mail_slate(week_no: int, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.mail import slate_published_body

        week = get_week(db(), week_no)
        subject, body = slate_published_body(
            week_title=week["title"], lock_at=week["lock_at"], app_url=Config.PUBLIC_APP_URL
        )
        sent = []
        for email in list_invited_emails(db()):
            sent.append({"to": email, "delivery": _mail(email, subject, body)})
        return {"sent": len(sent)}

    @app.post("/api/admin/weeks/{week_no}/mail/reminder")
    def admin_mail_reminder(week_no: int, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.mail import lock_reminder_body

        week = get_week(db(), week_no)
        sent = 0
        for row in users_missing_picks(db(), week["id"]):
            if not row["email"]:
                continue
            subject, body = lock_reminder_body(
                week_title=week["title"],
                lock_at=week["lock_at"],
                have=int(row["n"]),
                app_url=Config.PUBLIC_APP_URL,
            )
            _mail(row["email"], subject, body)
            sent += 1
        return {"sent": sent}

    @app.post("/api/admin/weeks/{week_no}/mail/standings")
    def admin_mail_standings(week_no: int, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.mail import standings_body

        week = get_week(db(), week_no)
        table = standings(db())
        lines = [f"{row['rank']}. {row['display_name']}  {row['record']}" for row in table["table"]]
        subject, body = standings_body(
            week_title=week["title"], table_text="\n".join(lines), app_url=Config.PUBLIC_APP_URL
        )
        sent = 0
        for email in list_invited_emails(db()):
            _mail(email, subject, body)
            sent += 1
        return {"sent": sent}

    if frontend is not None:
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def landing():
            return FileResponse(frontend / "index.html")

        @app.get("/app/{full_path:path}")
        def spa(full_path: str):
            return FileResponse(frontend / "index.html")

        @app.get("/app")
        def spa_root():
            return FileResponse(frontend / "index.html")
    else:

        @app.get("/")
        def landing_fallback():
            return JSONResponse(
                {
                    "service": "cfbsicko",
                    "hint": "Build the Vue app (cd frontend && npm run build) or open /api/health",
                }
            )

    return app


app = create_app()


@app.exception_handler(NotFoundError)
async def _not_found(_request: Request, exc: NotFoundError):
    return JSONResponse({"detail": str(exc)}, status_code=404)
