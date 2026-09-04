"""In-process scheduler: jobs, outbox, odds overlay, live scores."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from cfbsicko.config import Config
from cfbsicko.feed import EmptyFeed, FeedGame, ScoreOddsFeed
from cfbsicko.mail import (
    branded_html,
    line_moved_body,
    lineup_saved_body,
    lock_warning_complete_body,
    lock_warning_incomplete_body,
    slate_published_body,
    standings_body,
)
from cfbsicko.rules import EASTERN
from cfbsicko.store import (
    get_week,
    grade_week,
    list_games,
    list_invited_emails,
    parse_lock_at,
    set_game_result,
    week_is_writable,
    write_snapshot,
)

SendFn = Callable[..., str]
LINE_MOVE_DELTA = 0.5
OUTBOX_BATCH = 20
OUTBOX_MAX_ATTEMPTS = 8


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=EASTERN)
    return value


def _parse_when(value: str) -> datetime:
    return _as_aware(parse_lock_at(value))


def schedule_lock_jobs(conn: sqlite3.Connection, week: dict[str, Any]) -> None:
    lock_at = _parse_when(week["lock_at"])
    warning_at = lock_at - timedelta(hours=1)
    _upsert_job(conn, int(week["id"]), "lock_warning_1h", warning_at)
    _upsert_job(conn, int(week["id"]), "lock_snapshot", lock_at)


def _upsert_job(conn: sqlite3.Connection, week_id: int, kind: str, run_at: datetime) -> None:
    iso = run_at.isoformat()
    conn.execute(
        """
        INSERT INTO scheduled_jobs (week_id, kind, run_at, status)
        VALUES (?, ?, ?, 'pending')
        ON CONFLICT(week_id, kind) DO UPDATE SET
            run_at = excluded.run_at
        WHERE scheduled_jobs.status = 'pending'
        """,
        (week_id, kind, iso),
    )


def reschedule_lock_jobs(conn: sqlite3.Connection, week: dict[str, Any]) -> None:
    schedule_lock_jobs(conn, week)


def copy_freeze_overlay(conn: sqlite3.Connection, week_id: int) -> None:
    conn.execute(
        """
        UPDATE games SET
            market_spread_home = spread_home,
            market_total = total,
            market_updated_at = datetime('now'),
            market_source = 'freeze'
        WHERE week_id = ?
        """,
        (week_id,),
    )


def enqueue_mail(
    conn: sqlite3.Connection,
    *,
    kind: str,
    to_email: str,
    subject: str,
    body: str,
    html: str | None = None,
    week_id: int | None = None,
    league_id: int | None = None,
    dedupe_key: str,
    send_after: datetime | None = None,
    user_id: int | None = None,
    title: str | None = None,
    href: str = "/app",
) -> bool:
    payload = {"subject": subject, "body": body, "html": html or branded_html(subject, body, href=href)}
    when = (send_after or datetime(1970, 1, 1, tzinfo=EASTERN)).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO mail_outbox (
            kind, week_id, league_id, to_email, payload_json, dedupe_key, send_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, week_id, league_id, to_email, json.dumps(payload), dedupe_key, when),
    )
    inserted = cur.rowcount > 0
    if inserted and user_id is not None:
        conn.execute(
            """
            INSERT INTO notifications (user_id, kind, title, body, href)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, kind, title or subject, body, href),
        )
    return inserted


def enqueue_lock_warnings(conn: sqlite3.Connection, week: dict[str, Any]) -> int:
    from cfbsicko.leagues import default_league_id

    leagues = [dict(row) for row in conn.execute("SELECT id FROM leagues")]
    if not leagues:
        leagues = [{"id": default_league_id(conn)}]
    sent = 0
    href = f"{Config.PUBLIC_APP_URL}/app"
    for league in leagues:
        league_id = int(league["id"])
        for row in _pick_counts(conn, int(week["id"]), league_id):
            email = (row["email"] or "").strip()
            if not email:
                continue
            have = int(row["n"])
            if have < 5:
                subject, body = lock_warning_incomplete_body(
                    week_title=week["title"],
                    lock_at=week["lock_at"],
                    have=have,
                    app_url=Config.PUBLIC_APP_URL,
                )
                kind = "lock_warning_1h"
            else:
                subject, body = lock_warning_complete_body(
                    week_title=week["title"], lock_at=week["lock_at"], app_url=Config.PUBLIC_APP_URL
                )
                kind = "lock_warning_1h_complete"
            if enqueue_mail(
                conn,
                kind=kind,
                to_email=email,
                subject=subject,
                body=body,
                week_id=int(week["id"]),
                league_id=league_id,
                dedupe_key=week["lock_at"],
                user_id=int(row["id"]) if row["id"] else None,
                title=subject,
                href=href,
            ):
                sent += 1
    return sent


def enqueue_slate_mail(
    conn: sqlite3.Connection, week: dict[str, Any], *, league_id: int | None = None
) -> int:
    subject, body = slate_published_body(
        week_title=week["title"], lock_at=week["lock_at"], app_url=Config.PUBLIC_APP_URL
    )
    sent = 0
    leagues = (
        [{"id": league_id}]
        if league_id is not None
        else [dict(row) for row in conn.execute("SELECT id FROM leagues")]
    )
    for league in leagues:
        lid = int(league["id"])
        for email in list_invited_emails(conn, league_id=lid):
            if enqueue_mail(
                conn,
                kind="slate",
                to_email=email,
                subject=subject,
                body=body,
                week_id=int(week["id"]),
                league_id=lid,
                dedupe_key=week["lock_at"],
                href=f"{Config.PUBLIC_APP_URL}/app",
            ):
                sent += 1
    return sent


def enqueue_standings_mail(conn: sqlite3.Connection, week: dict[str, Any]) -> int:
    from cfbsicko.store import standings

    sent = 0
    for league in conn.execute("SELECT id FROM leagues"):
        league_id = int(league["id"])
        table = standings(conn, league_id=league_id)
        lines = [f"{row['rank']}. {row['display_name']}  {row['record']}" for row in table["table"]]
        subject, body = standings_body(
            week_title=week["title"], table_text="\n".join(lines), app_url=Config.PUBLIC_APP_URL
        )
        for email in list_invited_emails(conn, league_id=league_id):
            if enqueue_mail(
                conn,
                kind="standings",
                to_email=email,
                subject=subject,
                body=body,
                week_id=int(week["id"]),
                league_id=league_id,
                dedupe_key="graded",
                href=f"{Config.PUBLIC_APP_URL}/app/standings",
            ):
                sent += 1
    return sent


def _pick_counts(conn: sqlite3.Connection, week_id: int, league_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT u.id, u.display_name, u.email, COUNT(p.id) AS n
        FROM users u
        JOIN league_members m ON m.user_id = u.id AND m.league_id = ?
        LEFT JOIN picks p ON p.user_id = u.id AND p.week_id = ?
        GROUP BY u.id
        ORDER BY u.display_name
        """,
        (league_id, week_id),
    ).fetchall()
    return [dict(row) for row in rows]


def tick_jobs(conn: sqlite3.Connection, now: datetime) -> int:
    now = _as_aware(now)
    rows = conn.execute(
        "SELECT * FROM scheduled_jobs WHERE status = 'pending' AND locked_at IS NULL ORDER BY run_at"
    ).fetchall()
    ran = 0
    for row in rows:
        if _parse_when(row["run_at"]) > now:
            continue
        conn.execute(
            "UPDATE scheduled_jobs SET locked_at = ?, attempts = attempts + 1 WHERE id = ?",
            (now.isoformat(), row["id"]),
        )
        try:
            _run_job(conn, dict(row), now)
            conn.execute(
                "UPDATE scheduled_jobs SET status = 'done', locked_at = NULL, last_error = NULL WHERE id = ?",
                (row["id"],),
            )
        except Exception as exc:
            conn.execute(
                "UPDATE scheduled_jobs SET status = 'error', locked_at = NULL, last_error = ? WHERE id = ?",
                (str(exc)[:500], row["id"]),
            )
        ran += 1
    conn.commit()
    return ran


def _run_job(conn: sqlite3.Connection, job: dict[str, Any], now: datetime) -> None:
    week = conn.execute("SELECT * FROM weeks WHERE id = ?", (job["week_id"],)).fetchone()
    if week is None:
        return
    week_d = dict(week)
    if job["kind"] == "lock_warning_1h":
        enqueue_lock_warnings(conn, week_d)
        return
    if job["kind"] == "lock_snapshot":
        existing = conn.execute(
            "SELECT 1 FROM week_snapshots WHERE week_id = ? AND kind = 'lock'",
            (week_d["id"],),
        ).fetchone()
        if existing is None:
            write_snapshot(conn, int(week_d["id"]), "lock")
        return
    if job["kind"] == "standings_mail":
        enqueue_standings_mail(conn, week_d)


def tick_outbox(conn: sqlite3.Connection, now: datetime, send: SendFn) -> int:
    now = _as_aware(now)
    rows = conn.execute(
        """
        SELECT * FROM mail_outbox
        WHERE sent_at IS NULL AND attempts < ?
        ORDER BY id
        LIMIT ?
        """,
        (OUTBOX_MAX_ATTEMPTS, OUTBOX_BATCH),
    ).fetchall()
    sent = 0
    for row in rows:
        if _parse_when(row["send_after"]) > now:
            continue
        payload = json.loads(row["payload_json"])
        try:
            try:
                send(row["to_email"], payload["subject"], payload["body"], html=payload.get("html"))
            except TypeError:
                send(row["to_email"], payload["subject"], payload["body"])
            conn.execute(
                "UPDATE mail_outbox SET sent_at = ?, last_error = NULL WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
            sent += 1
        except Exception as exc:
            attempts = int(row["attempts"]) + 1
            delay = min(60, 2**attempts)
            nxt = now + timedelta(minutes=delay)
            conn.execute(
                """
                UPDATE mail_outbox SET attempts = ?, send_after = ?, last_error = ?
                WHERE id = ?
                """,
                (attempts, nxt.isoformat(), str(exc)[:500], row["id"]),
            )
    conn.commit()
    return sent


def in_odds_window(now: datetime) -> bool:
    local = _as_aware(now).astimezone(EASTERN)
    if local.weekday() == 1 and local.hour >= 10:
        return True
    if local.weekday() == 2:
        return True
    return local.weekday() == 3 and (local.hour, local.minute) < (18, 0)


def in_scores_window(now: datetime, *, any_in_progress: bool) -> bool:
    if any_in_progress:
        return True
    local = _as_aware(now).astimezone(EASTERN)
    if local.weekday() == 4 and local.hour >= 12:
        return True
    if local.weekday() in {5, 6}:
        return True
    return local.weekday() == 0 and local.hour < 8


def tick_odds(
    conn: sqlite3.Connection,
    now: datetime,
    feed: ScoreOddsFeed,
    *,
    season: int | None = None,
) -> int:
    now = _as_aware(now)
    if not in_odds_window(now):
        return 0
    week = _open_or_draft_week(conn, season)
    if week is None:
        return 0
    if week["status"] != "draft" and not week_is_writable(week, now):
        return 0
    games = [g for g in list_games(conn, week["id"]) if g.get("provider_game_id")]
    if not games:
        return 0
    finals = {
        int(row["game_id"])
        for row in conn.execute(
            "SELECT game_id FROM game_results WHERE status = 'final' AND game_id IN ({})".format(
                ",".join("?" * len(games))
            ),
            [g["id"] for g in games],
        )
    }
    liveable = [g for g in games if int(g["id"]) not in finals]
    if not liveable:
        return 0
    ticks = feed.odds([str(g["provider_game_id"]) for g in liveable])
    by_id = {item.provider_game_id: item for item in ticks}
    moved = 0
    for game in liveable:
        item = by_id.get(str(game["provider_game_id"]))
        if item is None:
            continue
        prev_spread = game.get("market_spread_home")
        prev_total = game.get("market_total")
        spread_delta = abs(float(item.spread_home) - float(game["spread_home"]))
        total_delta = abs(float(item.total) - float(game["total"]))
        changed = (
            prev_spread is None
            or prev_total is None
            or abs(float(item.spread_home) - float(prev_spread)) >= LINE_MOVE_DELTA
            or abs(float(item.total) - float(prev_total)) >= LINE_MOVE_DELTA
        )
        if not changed:
            continue
        conn.execute(
            """
            UPDATE games SET
                market_spread_home = ?, market_total = ?,
                market_updated_at = ?, market_source = ?
            WHERE id = ?
            """,
            (item.spread_home, item.total, now.isoformat(), "feed", game["id"]),
        )
        if (
            spread_delta >= LINE_MOVE_DELTA
            or total_delta >= LINE_MOVE_DELTA
            or (
                prev_spread is not None
                and abs(float(item.spread_home) - float(prev_spread)) >= LINE_MOVE_DELTA
            )
            or (prev_total is not None and abs(float(item.total) - float(prev_total)) >= LINE_MOVE_DELTA)
        ):
            conn.execute(
                """
                INSERT OR IGNORE INTO line_ticks (game_id, captured_at, spread_home, total, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (game["id"], now.isoformat(), item.spread_home, item.total, "feed"),
            )
            if week_is_writable(week, now):
                _enqueue_line_moved(conn, week, game, item)
        moved += 1
    _prune_old_ticks(conn, week)
    conn.commit()
    return moved


def _enqueue_line_moved(
    conn: sqlite3.Connection, week: dict[str, Any], game: dict[str, Any], item: FeedGame
) -> None:
    holders = conn.execute(
        """
        SELECT p.user_id, p.market, p.side, u.email
        FROM picks p
        JOIN users u ON u.id = p.user_id
        WHERE p.week_id = ? AND p.game_id = ?
        """,
        (week["id"], game["id"]),
    ).fetchall()
    for row in holders:
        email = (row["email"] or "").strip()
        if not email:
            continue
        legal = float(game["spread_home"] if row["market"] == "spread" else game["total"])
        market = float(item.spread_home if row["market"] == "spread" else item.total)
        if abs(market - legal) < LINE_MOVE_DELTA:
            continue
        subject, body = line_moved_body(
            away=game["away"],
            home=game["home"],
            market=row["market"],
            side=row["side"],
            legal=legal,
            market_line=market,
            app_url=Config.PUBLIC_APP_URL,
        )
        enqueue_mail(
            conn,
            kind="line_moved",
            to_email=email,
            subject=subject,
            body=body,
            week_id=int(week["id"]),
            dedupe_key=f"{game['id']}:{row['market']}:{market}",
            user_id=int(row["user_id"]),
            title=subject,
            href=f"{Config.PUBLIC_APP_URL}/app",
        )


def _prune_old_ticks(conn: sqlite3.Connection, week: dict[str, Any]) -> None:
    lock_at = week.get("lock_at")
    if not lock_at:
        return
    conn.execute(
        """
        DELETE FROM line_ticks WHERE game_id IN (
            SELECT id FROM games WHERE week_id = ?
        ) AND captured_at < ?
        """,
        (week["id"], lock_at),
    )


def tick_scores(
    conn: sqlite3.Connection,
    now: datetime,
    feed: ScoreOddsFeed,
    *,
    season: int | None = None,
) -> int:
    now = _as_aware(now)
    week = _currentish_week(conn, season)
    if week is None:
        return 0
    games = [g for g in list_games(conn, week["id"]) if g.get("provider_game_id")]
    any_live = any((g.get("game_status") or "") == "in_progress" for g in games)
    if not in_scores_window(now, any_in_progress=any_live):
        return 0
    if not games:
        return 0
    updates = feed.scores([str(g["provider_game_id"]) for g in games])
    by_id = {item.provider_game_id: item for item in updates}
    changed = 0
    for game in games:
        item = by_id.get(str(game["provider_game_id"]))
        if item is None or item.home_score is None or item.away_score is None:
            continue
        status = item.status or "in_progress"
        if (
            (game.get("game_status") or "") == "final"
            and status == "final"
            and int(game["home_score"] or 0) == int(item.home_score)
            and int(game["away_score"] or 0) == int(item.away_score)
        ):
            continue
        set_game_result(
            conn,
            int(game["id"]),
            home_score=int(item.home_score),
            away_score=int(item.away_score),
            entered_by=None,
            source="cfbd",
            status=status,
            period=item.period,
            clock=item.clock,
        )
        changed += 1
    if changed:
        grade_week(conn, int(week["week_no"]), season=week["season"], partial=True)
    return changed


def ingest_draft(
    conn: sqlite3.Connection,
    *,
    week_no: int,
    games: list[FeedGame],
    lock_at: str,
    season: int | None = None,
    title: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from cfbsicko.store import SlateConflictError

    if not games:
        raise ValueError("feed returned no games")
    season = season or Config.SEASON
    conn.execute(
        """
        INSERT INTO weeks (season, week_no, title, lock_at, status)
        VALUES (?, ?, ?, ?, 'draft')
        ON CONFLICT(season, week_no) DO UPDATE SET
            title = excluded.title,
            lock_at = excluded.lock_at
        """,
        (season, week_no, title or f"Week {week_no}", lock_at),
    )
    week = get_week(conn, week_no, season)
    existing_picks = int(
        conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (week["id"],)).fetchone()["n"]
    )
    if existing_picks and not force:
        raise SlateConflictError(
            f"Week {week_no} already has {existing_picks} picks. Refusing to replace live data."
        )
    if existing_picks and force:
        conn.execute("DELETE FROM picks WHERE week_id = ?", (week["id"],))
    conn.execute(
        "DELETE FROM game_results WHERE game_id IN (SELECT id FROM games WHERE week_id = ?)",
        (week["id"],),
    )
    conn.execute("DELETE FROM games WHERE week_id = ?", (week["id"],))
    conn.execute("UPDATE weeks SET status = 'draft' WHERE id = ?", (week["id"],))
    for order, game in enumerate(games):
        conn.execute(
            """
            INSERT INTO games (
                week_id, away, home, spread_home, total, day_label, sort_order,
                kickoff, provider_game_id, market_spread_home, market_total,
                market_updated_at, market_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'feed')
            """,
            (
                week["id"],
                game.away,
                game.home,
                game.spread_home,
                game.total,
                game.day_label,
                order,
                game.kickoff,
                game.provider_game_id,
                game.spread_home,
                game.total,
            ),
        )
    conn.commit()
    return get_week(conn, week_no, season)


def freeze_week(
    conn: sqlite3.Connection,
    week_no: int,
    *,
    season: int | None = None,
    feed: ScoreOddsFeed | None = None,
) -> dict[str, Any]:

    week = get_week(conn, week_no, season)
    copy_freeze_overlay(conn, int(week["id"]))
    if feed is not None:
        resolve_provider_ids(conn, int(week["id"]), feed.slate(int(week["season"]), int(week["week_no"])))
    conn.execute(
        "UPDATE weeks SET status = 'open', publish_at = datetime('now') WHERE id = ?",
        (week["id"],),
    )
    week = get_week(conn, week_no, season)
    schedule_lock_jobs(conn, week)
    enqueue_slate_mail(conn, week)
    conn.commit()
    return week


def resolve_provider_ids(conn: sqlite3.Connection, week_id: int, feed_games: list[FeedGame]) -> int:
    index = {(_norm(g.away), _norm(g.home)): g for g in feed_games}
    matched = 0
    for row in conn.execute("SELECT id, away, home FROM games WHERE week_id = ?", (week_id,)):
        hit = index.get((_norm(row["away"]), _norm(row["home"])))
        if hit is None:
            continue
        conn.execute(
            "UPDATE games SET provider_game_id = ?, kickoff = COALESCE(kickoff, ?) WHERE id = ?",
            (hit.provider_game_id, hit.kickoff, row["id"]),
        )
        matched += 1
    return matched


def set_provider_game_id(conn: sqlite3.Connection, game_id: int, provider_game_id: str) -> None:
    conn.execute("UPDATE games SET provider_game_id = ? WHERE id = ?", (provider_game_id.strip(), game_id))
    conn.commit()


def unmatched_games(conn: sqlite3.Connection, week_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, away, home FROM games
        WHERE week_id = ? AND (provider_game_id IS NULL OR provider_game_id = '')
        ORDER BY sort_order
        """,
        (week_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def outbox_failures(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kind, to_email, attempts, last_error, send_after
        FROM mail_outbox
        WHERE last_error IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_notifications(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, kind, title, body, href, created_at, read_at
            FROM notifications WHERE user_id = ?
            ORDER BY id DESC LIMIT 50
            """,
            (user_id,),
        )
    ]
    unread = sum(1 for row in rows if row["read_at"] is None)
    return {"items": rows, "unread": unread}


def mark_notification_read(conn: sqlite3.Connection, user_id: int, notification_id: int) -> bool:
    cur = conn.execute(
        "UPDATE notifications SET read_at = datetime('now') WHERE id = ? AND user_id = ?",
        (notification_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def enqueue_lineup_saved(
    conn: sqlite3.Connection,
    *,
    user: dict[str, Any],
    week: dict[str, Any],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> None:
    email = (user.get("email") or "").strip()
    if not email:
        return
    subject, body = lineup_saved_body(
        week_title=week["title"],
        before=before,
        after=after,
        app_url=Config.PUBLIC_APP_URL,
    )
    enqueue_mail(
        conn,
        kind="lineup_saved",
        to_email=email,
        subject=subject,
        body=body,
        week_id=int(week["id"]),
        dedupe_key=datetime.now(EASTERN).isoformat(),
        user_id=int(user["id"]),
        title=subject,
        href=f"{Config.PUBLIC_APP_URL}/app",
    )


def tick_all(
    conn: sqlite3.Connection,
    now: datetime,
    send: SendFn,
    feed: ScoreOddsFeed | None = None,
) -> dict[str, int]:
    feed = feed or EmptyFeed()
    jobs = tick_jobs(conn, now)
    outbox = tick_outbox(conn, now, send)
    odds = tick_odds(conn, now, feed)
    scores = tick_scores(conn, now, feed)
    return {"jobs": jobs, "outbox": outbox, "odds": odds, "scores": scores}


def _open_or_draft_week(conn: sqlite3.Connection, season: int | None) -> dict[str, Any] | None:
    season = season or Config.SEASON
    row = conn.execute(
        """
        SELECT * FROM weeks WHERE season = ? AND status IN ('draft', 'open')
        ORDER BY week_no DESC LIMIT 1
        """,
        (season,),
    ).fetchone()
    return dict(row) if row else None


def _currentish_week(conn: sqlite3.Connection, season: int | None) -> dict[str, Any] | None:
    season = season or Config.SEASON
    row = conn.execute(
        """
        SELECT * FROM weeks WHERE season = ? AND status IN ('open', 'locked', 'draft')
        ORDER BY week_no DESC LIMIT 1
        """,
        (season,),
    ).fetchone()
    return dict(row) if row else None


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())
