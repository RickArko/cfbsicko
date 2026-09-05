"""League persistence: invites, picks, lock, grade, standings, snapshots."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from typing import Any

from cfbsicko.auth import AuthenticatedUser
from cfbsicko.config import Config
from cfbsicko.parse import parse_slate
from cfbsicko.rules import (
    PickSpec,
    PickValidationError,
    WeeklyRecord,
    is_before_lock,
    payout_preview,
    result_for_pick,
    sort_standings,
    validate_pick_set,
)


class InviteRequiredError(PermissionError):
    """Authenticated but not on the allowlist."""


class LockClosedError(PermissionError):
    """Picks are locked."""


class NotFoundError(KeyError):
    """Missing week/game/user."""


class SlateConflictError(ValueError):
    """Week already has picks; refuse unless force=True."""


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def parse_lock_at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def current_week(conn: sqlite3.Connection, season: int | None = None) -> dict[str, Any] | None:
    season = season or Config.SEASON
    row = conn.execute(
        """
        SELECT * FROM weeks
        WHERE season = ?
        ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'locked' THEN 1 WHEN 'graded' THEN 2 ELSE 3 END,
                 week_no DESC
        LIMIT 1
        """,
        (season,),
    ).fetchone()
    return _row(row)


def get_week(conn: sqlite3.Connection, week_no: int, season: int | None = None) -> dict[str, Any]:
    season = season or Config.SEASON
    row = conn.execute(
        "SELECT * FROM weeks WHERE season = ? AND week_no = ?",
        (season, week_no),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"week {week_no}")
    return dict(row)


def list_games(conn: sqlite3.Connection, week_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT g.*, r.home_score, r.away_score,
            r.status AS game_status, r.period, r.clock, r.source AS result_source
        FROM games g
        LEFT JOIN game_results r ON r.game_id = g.id
        WHERE g.week_id = ?
        ORDER BY g.sort_order, g.id
        """,
        (week_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_invited_user(conn: sqlite3.Connection, auth: AuthenticatedUser) -> dict[str, Any]:
    email = (auth.email or "").strip().lower()
    if not email:
        raise InviteRequiredError("token has no email")

    existing = conn.execute(
        "SELECT * FROM users WHERE supabase_user_id = ? OR email = ?",
        (auth.user_id, email),
    ).fetchone()
    commish = email in Config.commish_emails()
    invites = [dict(row) for row in conn.execute("SELECT * FROM invites WHERE email = ?", (email,))]
    named_invite = next((row for row in invites if row.get("display_name")), invites[0] if invites else None)

    if existing is None and not invites and not commish:
        raise InviteRequiredError("email is not invited")

    if existing is None:
        display = (
            named_invite["display_name"]
            if named_invite and named_invite["display_name"]
            else email.split("@")[0]
        )
        # Link a sheet-imported display-name row when the invite carries that name.
        named = None
        if named_invite and named_invite["display_name"]:
            named = conn.execute(
                "SELECT * FROM users WHERE display_name = ? AND supabase_user_id IS NULL",
                (named_invite["display_name"],),
            ).fetchone()
        if named:
            conn.execute(
                """
                UPDATE users SET supabase_user_id = ?, email = ?, is_commish = ?
                WHERE id = ?
                """,
                (auth.user_id, email, 1 if commish else named["is_commish"], named["id"]),
            )
            user_id = named["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO users (supabase_user_id, email, display_name, is_commish)
                VALUES (?, ?, ?, ?)
                """,
                (auth.user_id, email, display, 1 if commish else 0),
            )
            user_id = cur.lastrowid
    else:
        conn.execute(
            """
            UPDATE users SET supabase_user_id = ?, email = ?,
                is_commish = CASE WHEN ? THEN 1 ELSE is_commish END
            WHERE id = ?
            """,
            (auth.user_id, email, 1 if commish else 0, existing["id"]),
        )
        user_id = existing["id"]

    from cfbsicko.leagues import add_member, default_league_id, sync_default_league_members

    sync_default_league_members(conn)
    add_member(conn, default_league_id(conn), int(user_id), role="commish" if commish else "player")
    for invite in invites:
        if invite.get("accepted_at") is None:
            conn.execute("UPDATE invites SET accepted_at = datetime('now') WHERE id = ?", (invite["id"],))
        lid = invite.get("league_id")
        if not lid:
            continue
        invite_role = invite.get("role") or "player"
        if invite_role not in {"player", "commish"}:
            invite_role = "player"
        add_member(
            conn,
            int(lid),
            int(user_id),
            role="commish" if commish else invite_role,
        )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row is not None
    return dict(row)


def create_invite(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str | None,
    invited_by: int | None,
    league_id: int | None = None,
    role: str = "player",
) -> dict[str, Any]:
    from cfbsicko.leagues import add_member, default_league_id

    email_n = email.strip().lower()
    if role not in {"player", "commish"}:
        raise ValueError("role must be player or commish")
    league_id = default_league_id(conn) if league_id is None else int(league_id)
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO invites (email, display_name, token_hash, invited_by, league_id, role)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(email, league_id) DO UPDATE SET
            display_name = excluded.display_name,
            token_hash = excluded.token_hash,
            invited_by = excluded.invited_by,
            role = excluded.role,
            accepted_at = NULL
        """,
        (email_n, display_name, token_hash, invited_by, league_id, role),
    )
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email_n,)).fetchone()
    if existing:
        add_member(conn, league_id, int(existing["id"]), role=role)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM invites WHERE email = ? AND league_id = ?",
        (email_n, league_id),
    ).fetchone()
    assert row is not None
    payload = dict(row)
    payload["token"] = token
    return payload


def week_is_writable(week: dict[str, Any], now: datetime) -> bool:
    if week["status"] != "open":
        return False
    return is_before_lock(now, parse_lock_at(week["lock_at"]))


def save_picks(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    week: dict[str, Any],
    picks: list[PickSpec],
    now: datetime,
) -> list[dict[str, Any]]:
    if not week_is_writable(week, now):
        raise LockClosedError("week is locked")
    validate_pick_set(picks)
    games = {row["id"]: row for row in list_games(conn, week["id"])}
    for pick in picks:
        if pick.game_id not in games:
            raise PickValidationError(f"game {pick.game_id} is not on this slate")
    prior = list_user_picks(conn, user_id, week["id"])
    conn.execute("DELETE FROM picks WHERE user_id = ? AND week_id = ?", (user_id, week["id"]))
    for pick in picks:
        conn.execute(
            """
            INSERT INTO picks (user_id, week_id, slot, game_id, market, side)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, week["id"], pick.slot, pick.game_id, pick.market, pick.side),
        )
    saved = list_user_picks(conn, user_id, week["id"])
    conn.execute(
        "INSERT INTO pick_revisions (user_id, week_id, payload_json) VALUES (?, ?, ?)",
        (user_id, week["id"], json.dumps(saved, default=str)),
    )
    if prior:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        from cfbsicko.jobs import enqueue_lineup_saved

        enqueue_lineup_saved(
            conn,
            user=dict(user) if user else {"id": user_id, "email": None},
            week=week,
            before=prior,
            after=saved,
        )
    conn.commit()
    return saved


def list_user_picks(conn: sqlite3.Connection, user_id: int, week_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*, g.away, g.home, g.spread_home, g.total, g.day_label
        FROM picks p
        JOIN games g ON g.id = p.game_id
        WHERE p.user_id = ? AND p.week_id = ?
        ORDER BY p.slot
        """,
        (user_id, week_id),
    ).fetchall()
    return [dict(row) for row in rows]


def board(conn: sqlite3.Connection, week: dict[str, Any], now: datetime) -> list[dict[str, Any]] | None:
    if week_is_writable(week, now) and week["status"] == "open":
        return None
    users = conn.execute(
        """
        SELECT u.id, u.display_name
        FROM users u
        ORDER BY u.display_name
        """
    ).fetchall()
    out = []
    for user in users:
        out.append(
            {
                "user_id": user["id"],
                "display_name": user["display_name"],
                "picks": list_user_picks(conn, user["id"], week["id"]),
            }
        )
    return out


def publish_slate(
    conn: sqlite3.Connection,
    *,
    week_no: int,
    slate_text: str,
    lock_at: str,
    season: int | None = None,
    title: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    season = season or Config.SEASON
    parse_lock_at(lock_at)
    games = parse_slate(slate_text)
    if not games:
        raise ValueError("slate contained no games")
    existing = conn.execute(
        "SELECT id FROM weeks WHERE season = ? AND week_no = ?",
        (season, week_no),
    ).fetchone()
    if existing:
        pick_n = int(
            conn.execute("SELECT COUNT(*) AS n FROM picks WHERE week_id = ?", (existing["id"],)).fetchone()[
                "n"
            ]
        )
        if pick_n and not force:
            raise SlateConflictError(
                f"Week {week_no} already has {pick_n} picks. Refusing to replace live data."
            )
    conn.execute(
        """
        INSERT INTO weeks (season, week_no, title, lock_at, status, publish_at)
        VALUES (?, ?, ?, ?, 'open', datetime('now'))
        ON CONFLICT(season, week_no) DO UPDATE SET
            title = excluded.title,
            lock_at = excluded.lock_at,
            status = 'open',
            publish_at = datetime('now')
        """,
        (season, week_no, title or f"Week {week_no}", lock_at),
    )
    week = get_week(conn, week_no, season)
    conn.execute("DELETE FROM picks WHERE week_id = ?", (week["id"],))
    conn.execute("DELETE FROM week_records WHERE week_id = ?", (week["id"],))
    conn.execute(
        "DELETE FROM game_results WHERE game_id IN (SELECT id FROM games WHERE week_id = ?)",
        (week["id"],),
    )
    conn.execute("DELETE FROM games WHERE week_id = ?", (week["id"],))
    for order, game in enumerate(games):
        conn.execute(
            """
            INSERT INTO games (
                week_id, away, home, spread_home, total, day_label, sort_order,
                market_spread_home, market_total, market_updated_at, market_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'freeze')
            """,
            (
                week["id"],
                game.away,
                game.home,
                game.spread_home,
                game.total,
                game.day_label,
                order,
                game.spread_home,
                game.total,
            ),
        )
    from cfbsicko.jobs import schedule_lock_jobs

    schedule_lock_jobs(conn, week)
    conn.commit()
    return get_week(conn, week_no, season)


def update_week(
    conn: sqlite3.Connection,
    week_no: int,
    *,
    lock_at: str | None = None,
    status: str | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    week = get_week(conn, week_no, season)
    if lock_at:
        parse_lock_at(lock_at)
        conn.execute("UPDATE weeks SET lock_at = ? WHERE id = ?", (lock_at, week["id"]))
    if status:
        if status not in {"draft", "open", "locked", "graded"}:
            raise ValueError(f"invalid status {status}")
        conn.execute("UPDATE weeks SET status = ? WHERE id = ?", (status, week["id"]))
    conn.commit()
    week = get_week(conn, week_no, season)
    if lock_at:
        from cfbsicko.jobs import reschedule_lock_jobs

        reschedule_lock_jobs(conn, week)
        conn.commit()
    return week


def set_game_result(
    conn: sqlite3.Connection,
    game_id: int,
    *,
    home_score: int,
    away_score: int,
    entered_by: int | None,
    source: str = "manual",
    status: str = "final",
    period: str | None = None,
    clock: str | None = None,
) -> dict[str, Any]:
    if status not in {"scheduled", "in_progress", "final"}:
        raise ValueError("status must be scheduled, in_progress, or final")
    before = _row(conn.execute("SELECT * FROM game_results WHERE game_id = ?", (game_id,)).fetchone())
    conn.execute(
        """
        INSERT INTO game_results (
            game_id, home_score, away_score, source, entered_by, status, period, clock, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id) DO UPDATE SET
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            source = excluded.source,
            entered_by = excluded.entered_by,
            status = excluded.status,
            period = excluded.period,
            clock = excluded.clock,
            entered_at = datetime('now'),
            updated_at = datetime('now')
        """,
        (game_id, home_score, away_score, source, entered_by, status, period, clock),
    )
    after = dict(conn.execute("SELECT * FROM game_results WHERE game_id = ?", (game_id,)).fetchone())
    write_audit(
        conn,
        actor_user_id=entered_by,
        action="set_score",
        entity="game_results",
        entity_id=str(game_id),
        before=before,
        after=after,
    )
    conn.commit()
    return after


def grade_week(
    conn: sqlite3.Connection,
    week_no: int,
    *,
    season: int | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    week = get_week(conn, week_no, season)
    games = {g["id"]: g for g in list_games(conn, week["id"])}
    picks = conn.execute("SELECT * FROM picks WHERE week_id = ?", (week["id"],)).fetchall()
    for pick in picks:
        game = games[pick["game_id"]]
        if game["home_score"] is None or game["away_score"] is None:
            continue
        status = game.get("game_status") or "final"
        if partial and status != "final":
            continue
        result = result_for_pick(
            market=pick["market"],
            side=pick["side"],
            home_score=int(game["home_score"]),
            away_score=int(game["away_score"]),
            spread_home=float(game["spread_home"]),
            total=float(game["total"]),
            override=pick["override_result"],
        )
        conn.execute("UPDATE picks SET result = ? WHERE id = ?", (result, pick["id"]))
    users = conn.execute("SELECT id FROM users").fetchall()
    for user in users:
        rows = conn.execute(
            "SELECT result FROM picks WHERE user_id = ? AND week_id = ?",
            (user["id"], week["id"]),
        ).fetchall()
        wins = sum(1 for r in rows if r["result"] == "W")
        ties = sum(1 for r in rows if r["result"] == "T")
        losses = sum(1 for r in rows if r["result"] == "L")
        conn.execute(
            """
            INSERT INTO week_records (user_id, week_id, wins, ties, losses)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, week_id) DO UPDATE SET
                wins = excluded.wins, ties = excluded.ties, losses = excluded.losses
            """,
            (user["id"], week["id"], wins, ties, losses),
        )
    all_final = bool(games) and all(
        (g.get("game_status") or ("final" if g.get("home_score") is not None else None)) == "final"
        and g.get("home_score") is not None
        for g in games.values()
    )
    if not partial or all_final:
        conn.execute("UPDATE weeks SET status = 'graded' WHERE id = ?", (week["id"],))
        conn.commit()
        snap = write_snapshot(conn, week["id"], "grade")
        from cfbsicko.jobs import enqueue_standings_mail

        enqueue_standings_mail(conn, get_week(conn, week_no, season))
        conn.commit()
        return {"week": get_week(conn, week_no, season), "snapshot_id": snap["id"]}
    conn.commit()
    return {"week": get_week(conn, week_no, season), "snapshot_id": None}


def override_pick(
    conn: sqlite3.Connection,
    pick_id: int,
    result: str,
    *,
    actor_user_id: int | None,
) -> dict[str, Any]:
    if result not in {"W", "T", "L"}:
        raise ValueError("override must be W, T, or L")
    before = _row(conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone())
    if before is None:
        raise NotFoundError("pick")
    conn.execute(
        "UPDATE picks SET override_result = ?, result = ? WHERE id = ?",
        (result, result, pick_id),
    )
    after = dict(conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone())
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action="override_pick",
        entity="picks",
        entity_id=str(pick_id),
        before=before,
        after=after,
    )
    conn.commit()
    return after


def set_paid(
    conn: sqlite3.Connection, user_id: int, paid: bool, *, league_id: int | None = None
) -> dict[str, Any]:
    from cfbsicko.leagues import default_league_id

    league_id = league_id or default_league_id(conn)
    member = conn.execute(
        "SELECT 1 FROM league_members WHERE league_id = ? AND user_id = ?",
        (league_id, user_id),
    ).fetchone()
    if member is None:
        raise NotFoundError("user")
    conn.execute(
        "UPDATE league_members SET buy_in_paid = ? WHERE league_id = ? AND user_id = ?",
        (1 if paid else 0, league_id, user_id),
    )
    default_id = default_league_id(conn)
    if league_id == default_id:
        conn.execute("UPDATE users SET buy_in_paid = ? WHERE id = ?", (1 if paid else 0, user_id))
    conn.commit()
    row = conn.execute(
        """
        SELECT u.id, u.display_name, u.email, u.is_commish, m.buy_in_paid, m.role
        FROM users u
        JOIN league_members m ON m.user_id = u.id
        WHERE u.id = ? AND m.league_id = ?
        """,
        (user_id, league_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("user")
    return dict(row)


def standings(
    conn: sqlite3.Connection, season: int | None = None, *, league_id: int | None = None
) -> dict[str, Any]:
    from cfbsicko.leagues import default_league_id, get_league

    season = season or Config.SEASON
    league_id = league_id or default_league_id(conn)
    league = get_league(conn, league_id)
    rows = conn.execute(
        """
        SELECT u.id AS user_id, u.display_name, m.buy_in_paid,
               COALESCE(SUM(wr.wins), 0) AS wins,
               COALESCE(SUM(wr.ties), 0) AS ties,
               COALESCE(SUM(wr.losses), 0) AS losses
        FROM league_members m
        JOIN users u ON u.id = m.user_id
        LEFT JOIN week_records wr ON wr.user_id = u.id
        LEFT JOIN weeks w ON w.id = wr.week_id AND w.season = ?
        WHERE m.league_id = ?
        GROUP BY u.id
        """,
        (season, league_id),
    ).fetchall()
    records = [
        WeeklyRecord(
            user_id=row["user_id"],
            display_name=row["display_name"],
            wins=int(row["wins"]),
            ties=int(row["ties"]),
            losses=int(row["losses"]),
            buy_in_paid=bool(row["buy_in_paid"]),
        )
        for row in rows
    ]
    ranked = sort_standings(records)
    paid = sum(1 for r in ranked if r.buy_in_paid)
    weekly = conn.execute(
        """
        SELECT wr.*, u.display_name, w.week_no
        FROM week_records wr
        JOIN users u ON u.id = wr.user_id
        JOIN weeks w ON w.id = wr.week_id
        JOIN league_members m ON m.user_id = u.id AND m.league_id = ?
        WHERE w.season = ?
        ORDER BY w.week_no, u.display_name
        """,
        (league_id, season),
    ).fetchall()
    payout = payout_preview(
        paid,
        buy_in=int(league["buy_in"]),
        shares=(float(league["pot_first"]), float(league["pot_second"]), float(league["pot_third"])),
        extra_owed=int(league["extra_owed"]),
    )
    return {
        "season": season,
        "league": league,
        "table": [
            {
                "rank": idx + 1,
                "user_id": r.user_id,
                "display_name": r.display_name,
                "wins": r.wins,
                "ties": r.ties,
                "losses": r.losses,
                "record": r.label,
                "buy_in_paid": r.buy_in_paid,
            }
            for idx, r in enumerate(ranked)
        ],
        "weekly": [dict(row) for row in weekly],
        "payout": {**payout.__dict__, "buy_in": int(league["buy_in"]), "bottom_n": int(league["bottom_n"])},
    }


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_user_id: int | None,
    action: str,
    entity: str,
    entity_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (actor_user_id, action, entity, entity_id, before_json, after_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor_user_id,
            action,
            entity,
            entity_id,
            json.dumps(before) if before else None,
            json.dumps(after) if after else None,
        ),
    )


def write_snapshot(conn: sqlite3.Connection, week_id: int, kind: str) -> dict[str, Any]:
    week = dict(conn.execute("SELECT * FROM weeks WHERE id = ?", (week_id,)).fetchone())
    payload = {
        "week": week,
        "games": list_games(conn, week_id),
        "picks": [dict(r) for r in conn.execute("SELECT * FROM picks WHERE week_id = ?", (week_id,))],
        "records": [
            dict(r) for r in conn.execute("SELECT * FROM week_records WHERE week_id = ?", (week_id,))
        ],
    }
    cur = conn.execute(
        "INSERT INTO week_snapshots (week_id, kind, payload_json) VALUES (?, ?, ?)",
        (week_id, kind, json.dumps(payload)),
    )
    conn.commit()
    return {"id": cur.lastrowid, "week_id": week_id, "kind": kind}


def list_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, week_id, kind, created_at FROM week_snapshots ORDER BY id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM week_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise NotFoundError("snapshot")
    payload = json.loads(row["payload_json"])
    return {
        "id": row["id"],
        "week_id": row["week_id"],
        "kind": row["kind"],
        "created_at": row["created_at"],
        **payload,
    }


def users_missing_picks(
    conn: sqlite3.Connection, week_id: int, *, league_id: int | None = None
) -> list[dict[str, Any]]:
    from cfbsicko.leagues import default_league_id

    league_id = league_id or default_league_id(conn)
    rows = conn.execute(
        """
        SELECT u.id, u.display_name, u.email, COUNT(p.id) AS n
        FROM users u
        JOIN league_members m ON m.user_id = u.id AND m.league_id = ?
        LEFT JOIN picks p ON p.user_id = u.id AND p.week_id = ?
        GROUP BY u.id
        HAVING n < 5
        ORDER BY u.display_name
        """,
        (league_id, week_id),
    ).fetchall()
    return [dict(row) for row in rows]


def list_users(conn: sqlite3.Connection, *, league_id: int | None = None) -> list[dict[str, Any]]:
    from cfbsicko.leagues import default_league_id

    league_id = league_id or default_league_id(conn)
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT u.id, u.display_name, u.email, u.is_commish, m.buy_in_paid, m.role
            FROM users u
            JOIN league_members m ON m.user_id = u.id
            WHERE m.league_id = ?
            ORDER BY u.display_name
            """,
            (league_id,),
        )
    ]


def list_invited_emails(conn: sqlite3.Connection, *, league_id: int | None = None) -> list[str]:
    from cfbsicko.leagues import default_league_id

    league_id = league_id or default_league_id(conn)
    rows = conn.execute(
        """
        SELECT email FROM (
            SELECT u.email AS email
            FROM users u
            JOIN league_members m ON m.user_id = u.id
            WHERE m.league_id = ? AND u.email IS NOT NULL
            UNION
            SELECT i.email
            FROM invites i
            WHERE i.email IS NOT NULL AND COALESCE(i.league_id, ?) = ?
        )
        ORDER BY email
        """,
        (league_id, default_league_id(conn), league_id),
    )
    return [r["email"] for r in rows]
