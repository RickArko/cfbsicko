"""FastAPI app: health, auth config, picks, admin, standings. Serves the Vue dist."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cfbsicko.auth import AuthenticatedUser, check_local_password, get_auth_user, mint_local_token
from cfbsicko.config import Config
from cfbsicko.db import connect
from cfbsicko.feed import default_feed
from cfbsicko.leagues import (
    add_member,
    create_league,
    get_league,
    get_membership,
    is_league_commish,
    list_leagues_for_user,
    resolve_league_id,
    update_league,
)
from cfbsicko.rate_limit import RateLimiter
from cfbsicko.rules import PickSpec, PickValidationError
from cfbsicko.store import (
    InviteRequiredError,
    LockClosedError,
    NotFoundError,
    SlateConflictError,
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
    week_is_writable,
    write_snapshot,
)

log = logging.getLogger("cfbsicko.ticks")


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
    league_id: int | None = None


class LeagueIn(BaseModel):
    name: str
    buy_in: int = 75
    pot_first: float = 0.60
    pot_second: float = 0.30
    pot_third: float = 0.10
    extra_owed: int = 75
    bottom_n: int = 3


class LeaguePatch(BaseModel):
    name: str | None = None
    buy_in: int | None = None
    pot_first: float | None = None
    pot_second: float | None = None
    pot_third: float | None = None
    extra_owed: int | None = None
    bottom_n: int | None = None


class LeagueMemberIn(BaseModel):
    email: str
    display_name: str | None = None
    role: str = "player"

    def validated_role(self) -> str:
        if self.role not in {"player", "commish"}:
            raise ValueError("role must be player or commish")
        return self.role


class SlateIn(BaseModel):
    week_no: int
    slate_text: str
    lock_at: str
    title: str | None = None
    force: bool = False


class IngestIn(BaseModel):
    lock_at: str
    title: str | None = None
    force: bool = False


class ProviderIdIn(BaseModel):
    provider_game_id: str


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


async def _run_live_ticks(app: FastAPI) -> None:

    last_odds = 0.0
    last_scores = 0.0
    while True:
        try:
            loop = asyncio.get_running_loop()
            now_m = loop.time()
            conn = getattr(app.state, "conn", None)
            if conn is None:
                app.state.conn = connect(app.state.db_path)
                conn = app.state.conn

            def _send(to: str, subject: str, body: str, html: str | None = None) -> str:
                return _dispatch_mail(app, to, subject, body, html=html)

            from cfbsicko.jobs import tick_jobs, tick_odds, tick_outbox, tick_scores

            tick_jobs(conn, app.state.now_fn())
            tick_outbox(conn, app.state.now_fn(), _send)
            if now_m - last_odds >= 900:
                tick_odds(conn, app.state.now_fn(), app.state.feed)
                last_odds = now_m
            if now_m - last_scores >= 60:
                tick_scores(conn, app.state.now_fn(), app.state.feed)
                last_scores = now_m
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live tick failed")
        await asyncio.sleep(15)


def _dispatch_mail(app: FastAPI, to: str, subject: str, body: str, html: str | None = None) -> str:
    if app.state.mail_send is not None:
        try:
            return app.state.mail_send(to, subject, body, html=html)
        except TypeError:
            return app.state.mail_send(to, subject, body)
    from cfbsicko.mail import send_mail

    return send_mail(to, subject, body, html=html)


def create_app(
    *,
    db_path: Path | None = None,
    now_fn=None,
    mail_send=None,
    feed=None,
    live_ticks: bool | None = None,
    cron_token: str | None = None,
) -> FastAPI:
    ticks_on = live_ticks if live_ticks is not None else now_fn is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if ticks_on:
            task = asyncio.create_task(_run_live_ticks(_app))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        conn = getattr(_app.state, "conn", None)
        if conn is not None:
            conn.close()
            _app.state.conn = None

    app = FastAPI(title="CFB Sicko", version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path or Config.database_path()
    app.state.now_fn = now_fn or _now_utc
    app.state.mail_send = mail_send
    app.state.feed = feed if feed is not None else default_feed()
    app.state.cron_token = cron_token if cron_token is not None else Config.CRON_TOKEN
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
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.ProgrammingError:
                pass
        app.state.conn = connect(app.state.db_path)
        return app.state.conn

    def now() -> datetime:
        return app.state.now_fn()

    def league_user(auth: AuthenticatedUser = Depends(get_auth_user)) -> dict[str, Any]:
        try:
            return upsert_invited_user(db(), auth)
        except InviteRequiredError as exc:
            raise HTTPException(status_code=403, detail="Invite required") from exc

    def active_league(
        user: dict[str, Any] = Depends(league_user),
        x_league_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        requested = None
        if x_league_id:
            try:
                requested = int(x_league_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Bad league id") from exc
        try:
            league_id = resolve_league_id(db(), user, requested)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Not a member of that league") from exc
        return get_league(db(), league_id)

    def commish(
        user: dict[str, Any] = Depends(league_user),
        league: dict[str, Any] = Depends(active_league),
    ) -> dict[str, Any]:
        if is_league_commish(db(), user, int(league["id"])):
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
    def me(
        user: dict[str, Any] = Depends(league_user),
        league: dict[str, Any] = Depends(active_league),
    ):
        leagues = list_leagues_for_user(db(), user)
        member = get_membership(db(), int(league["id"]), int(user["id"]))
        return {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "is_commish": is_league_commish(db(), user, int(league["id"])),
            "buy_in_paid": bool(member["buy_in_paid"]) if member else False,
            "league": league,
            "leagues": leagues,
        }

    @app.get("/api/me/notifications")
    def me_notifications(user: dict[str, Any] = Depends(league_user)):
        from cfbsicko.jobs import list_notifications

        return list_notifications(db(), int(user["id"]))

    @app.post("/api/me/notifications/{notification_id}/read")
    def me_notification_read(notification_id: int, user: dict[str, Any] = Depends(league_user)):
        from cfbsicko.jobs import mark_notification_read

        if not mark_notification_read(db(), int(user["id"]), notification_id):
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"ok": True}

    @app.post("/api/internal/tick")
    def internal_tick(x_cron_token: str | None = Header(default=None, alias="X-Cron-Token")):
        token = app.state.cron_token
        if not token:
            raise HTTPException(status_code=404, detail="Not found")
        if x_cron_token != token:
            raise HTTPException(status_code=403, detail="Bad cron token")
        from cfbsicko.jobs import tick_all

        return tick_all(
            db(),
            now(),
            lambda to, subject, body, html=None: _dispatch_mail(app, to, subject, body, html=html),
            feed=app.state.feed,
        )

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
    def get_standings(
        user: dict[str, Any] = Depends(league_user),
        league: dict[str, Any] = Depends(active_league),
    ):
        return standings(db(), league_id=int(league["id"]))

    @app.get("/api/leagues")
    def get_leagues(user: dict[str, Any] = Depends(league_user)):
        return {"leagues": list_leagues_for_user(db(), user)}

    @app.post("/api/admin/leagues")
    def admin_create_league(body: LeagueIn, user: dict[str, Any] = Depends(commish)):
        try:
            return create_league(
                db(),
                name=body.name,
                created_by=user["id"],
                buy_in=body.buy_in,
                pot_first=body.pot_first,
                pot_second=body.pot_second,
                pot_third=body.pot_third,
                extra_owed=body.extra_owed,
                bottom_n=body.bottom_n,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/admin/leagues/{league_id}")
    def admin_patch_league(league_id: int, body: LeaguePatch, user: dict[str, Any] = Depends(commish)):
        try:
            return update_league(
                db(),
                league_id,
                name=body.name,
                buy_in=body.buy_in,
                pot_first=body.pot_first,
                pot_second=body.pot_second,
                pot_third=body.pot_third,
                extra_owed=body.extra_owed,
                bottom_n=body.bottom_n,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="League not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/leagues/{league_id}/members")
    def admin_add_member(league_id: int, body: LeagueMemberIn, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.mail import invite_body

        try:
            get_league(db(), league_id)
            role = body.validated_role()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="League not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invite = create_invite(
            db(),
            email=body.email,
            display_name=body.display_name,
            invited_by=user["id"],
            league_id=league_id,
            role=role,
        )
        existing = db().execute("SELECT id FROM users WHERE email = ?", (invite["email"],)).fetchone()
        if existing:
            add_member(db(), league_id, int(existing["id"]), role=role)
            db().commit()
        subject, text = invite_body(
            display_name=invite["display_name"],
            app_url=Config.PUBLIC_APP_URL,
        )
        mailed = False
        try:
            _mail(invite["email"], subject, text)
            mailed = True
        except Exception:
            mailed = False
        return {"email": invite["email"], "display_name": invite["display_name"], "mailed": mailed}

    @app.post("/api/admin/invites")
    def admin_invite(
        body: InviteIn,
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        from cfbsicko.mail import invite_body

        target_id = int(league["id"]) if body.league_id is None else body.league_id
        try:
            get_league(db(), target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="League not found") from exc
        invite = create_invite(
            db(),
            email=body.email,
            display_name=body.display_name,
            invited_by=user["id"],
            league_id=target_id,
        )
        subject, text = invite_body(
            display_name=invite["display_name"],
            app_url=Config.PUBLIC_APP_URL,
        )
        mailed = False
        try:
            _mail(invite["email"], subject, text)
            mailed = True
        except Exception:
            mailed = False
        return {
            "email": invite["email"],
            "display_name": invite["display_name"],
            "id": invite["id"],
            "mailed": mailed,
        }

    @app.post("/api/admin/weeks")
    def admin_publish(body: SlateIn, user: dict[str, Any] = Depends(commish)):
        try:
            week = publish_slate(
                db(),
                week_no=body.week_no,
                slate_text=body.slate_text,
                lock_at=body.lock_at,
                title=body.title,
                force=body.force,
            )
        except SlateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"week": week, "games": list_games(db(), week["id"])}

    @app.post("/api/admin/weeks/{week_no}/ingest")
    def admin_ingest(week_no: int, body: IngestIn, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.jobs import ingest_draft

        games = app.state.feed.slate(Config.SEASON, week_no)
        try:
            week = ingest_draft(
                db(),
                week_no=week_no,
                games=games,
                lock_at=body.lock_at,
                title=body.title,
                force=body.force,
            )
        except SlateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"week": week, "games": list_games(db(), week["id"])}

    @app.post("/api/admin/weeks/{week_no}/freeze")
    def admin_freeze(week_no: int, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.jobs import freeze_week, tick_outbox

        try:
            week = freeze_week(db(), week_no, feed=app.state.feed)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Week not found") from exc
        tick_outbox(
            db(),
            now(),
            lambda to, subject, body, html=None: _dispatch_mail(app, to, subject, body, html=html),
        )
        return {"week": week, "games": list_games(db(), week["id"])}

    @app.patch("/api/admin/games/{game_id}/provider")
    def admin_provider(game_id: int, body: ProviderIdIn, user: dict[str, Any] = Depends(commish)):
        from cfbsicko.jobs import set_provider_game_id

        set_provider_game_id(db(), game_id, body.provider_game_id)
        return {"ok": True}

    @app.get("/api/admin/live")
    def admin_live(user: dict[str, Any] = Depends(commish)):
        from cfbsicko.jobs import outbox_failures, unmatched_games

        week = current_week(db())
        if week is None:
            return {"week": None, "unmatched": [], "outbox_failures": outbox_failures(db())}
        return {
            "week": week,
            "unmatched": unmatched_games(db(), int(week["id"])),
            "outbox_failures": outbox_failures(db()),
        }

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
    def admin_user(
        user_id: int,
        body: PaidIn,
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        try:
            return set_paid(db(), user_id, body.buy_in_paid, league_id=int(league["id"]))
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc

    @app.get("/api/admin/users")
    def admin_users(
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        return {"users": list_users(db(), league_id=int(league["id"]))}

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
        return _dispatch_mail(app, to, subject, body)

    def _flush_outbox() -> int:
        from cfbsicko.jobs import tick_outbox

        return tick_outbox(
            db(),
            now(),
            lambda to, subject, body, html=None: _dispatch_mail(app, to, subject, body, html=html),
        )

    @app.post("/api/admin/weeks/{week_no}/mail/slate")
    def admin_mail_slate(
        week_no: int,
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        from cfbsicko.jobs import enqueue_slate_mail

        week = get_week(db(), week_no)
        queued = enqueue_slate_mail(db(), week, league_id=int(league["id"]))
        db().commit()
        _flush_outbox()
        return {"sent": queued}

    @app.post("/api/admin/weeks/{week_no}/mail/reminder")
    def admin_mail_reminder(
        week_no: int,
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        from cfbsicko.jobs import enqueue_lock_warnings

        week = get_week(db(), week_no)
        queued = enqueue_lock_warnings(db(), week, league_id=int(league["id"]))
        db().commit()
        _flush_outbox()
        return {"sent": queued}

    @app.post("/api/admin/weeks/{week_no}/mail/standings")
    def admin_mail_standings(
        week_no: int,
        user: dict[str, Any] = Depends(commish),
        league: dict[str, Any] = Depends(active_league),
    ):
        from cfbsicko.mail import standings_body

        week = get_week(db(), week_no)
        table = standings(db(), league_id=int(league["id"]))
        lines = [f"{row['rank']}. {row['display_name']}  {row['record']}" for row in table["table"]]
        subject, body = standings_body(
            week_title=week["title"], table_text="\n".join(lines), app_url=Config.PUBLIC_APP_URL
        )
        sent = 0
        for email in list_invited_emails(db(), league_id=int(league["id"])):
            _mail(email, subject, body)
            sent += 1
        return {"sent": sent}

    if frontend is not None:
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        def _dist_file(name: str) -> Path | None:
            path = (frontend / name).resolve()
            try:
                path.relative_to(frontend.resolve())
            except ValueError:
                return None
            return path if path.is_file() else None

        @app.get("/favicon.svg")
        def favicon_svg():
            path = _dist_file("favicon.svg")
            if path is None:
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(path)

        @app.get("/og.png")
        def og_png():
            path = _dist_file("og.png")
            if path is None:
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(path)

        @app.get("/og.svg")
        def og_svg():
            path = _dist_file("og.svg")
            if path is None:
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(path)

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
