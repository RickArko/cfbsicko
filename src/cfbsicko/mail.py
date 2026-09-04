"""Resend SMTP for slate / reminder / standings. Tests mock send_message."""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from cfbsicko.config import Config


class MailSender(Protocol):
    def send(self, message: EmailMessage) -> str: ...


@dataclass
class SmtpSender:
    host: str
    port: int
    user: str
    password: str
    use_ssl: bool = True
    use_tls: bool = False

    def send(self, message: EmailMessage) -> str:
        if self.use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=20)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=20)
        try:
            if self.use_tls:
                client.starttls()
            if self.user:
                client.login(self.user, self.password)
            client.send_message(message)
        finally:
            client.quit()
        return "smtp"


_sender_factory: Callable[[], MailSender] | None = None


def set_sender_factory(factory: Callable[[], MailSender] | None) -> None:
    global _sender_factory
    _sender_factory = factory


def get_sender() -> MailSender:
    if _sender_factory is not None:
        return _sender_factory()
    if not Config.SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP_PASSWORD is empty. Set the Resend API key in .env, or send from Fly "
            "where that secret already lives (fly ssh console -C 'cfbsicko invite-group --review')."
        )
    return SmtpSender(
        host=Config.SMTP_HOST,
        port=Config.SMTP_PORT,
        user=Config.SMTP_USER,
        password=Config.SMTP_PASSWORD,
        use_ssl=Config.SMTP_USE_SSL,
        use_tls=Config.SMTP_USE_TLS,
    )


def branded_html(heading: str, body: str, *, href: str, cta: str = "Lock your five") -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 12px;line-height:1.5">{_escape(line)}</p>' for line in body.split("\n") if line
    )
    return (
        '<!doctype html><html><body style="margin:0;background:#11100e;color:#f4efe6;'
        "font-family:Georgia,'Times New Roman',serif;padding:24px\">"
        '<div style="max-width:480px;margin:0 auto;background:#1c1914;border-radius:14px;'
        'padding:28px 24px">'
        '<p style="letter-spacing:.14em;text-transform:uppercase;font-size:12px;'
        'color:#e8a54b;margin:0 0 8px">CFB Sicko</p>'
        f'<h1 style="margin:0 0 16px;font-size:28px">{_escape(heading)}</h1>'
        f"{paragraphs}"
        f'<p style="margin:24px 0 0"><a href="{_escape(href)}" style="display:inline-block;'
        "background:#e8a54b;color:#11100e;padding:12px 18px;text-decoration:none;"
        f'border-radius:8px;font-weight:600">{_escape(cta)}</a></p>'
        "</div></body></html>"
    )


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _message(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    bcc: list[str] | None = None,
    html: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = Config.SMTP_FROM
    recipients = [to] if isinstance(to, str) else [part.strip() for part in to if part.strip()]
    msg["To"] = ", ".join(recipients)
    if bcc:
        msg["Bcc"] = ", ".join(part.strip() for part in bcc if part.strip())
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def group_invite_body(*, app_url: str) -> tuple[str, str]:
    subject = "The sheet is dead. Lock your five."
    body = (
        "The 2026 locks league lives at cfbsicko.com now. No spreadsheet.\n\n"
        "Five locks a week. Frozen Tuesday lines. Window closes Thursday 6pm ET.\n"
        "The board stays dark until lock.\n\n"
        f"If your email is on this list, sign in here:\n{app_url}/app\n\n"
        "We email a 6-digit code. Type it on that page.\n"
        "If you use ProtonMail, do not tap the link — Proton prefetches it and burns the code.\n\n"
        "House rules (same as always):\n"
        "$75 buy-in. Winner 60%, second 30%, third 10%.\n"
        "Bottom three each owe another $75 to one of the top three.\n"
        "FBS vs FCS counts. Conference championships and Army-Navy do not.\n\n"
        "See you Thursday.\n"
    )
    return subject, body


def group_invite_review_body(*, app_url: str, recipients: list[str]) -> tuple[str, str]:
    subject, body = group_invite_body(app_url=app_url)
    listed = "\n".join(f"  {email}" for email in recipients)
    wrapped = (
        "REVIEW ONLY — this has not gone to the league.\n"
        "If it looks right:  make invite-blast\n\n"
        f"Blast list ({len(recipients)}):\n{listed}\n\n"
        "---------- message below ----------\n\n"
        f"Subject: {subject}\n\n"
        f"{body}"
    )
    return f"[review] {subject}", wrapped


def invite_body(*, display_name: str | None, app_url: str) -> tuple[str, str]:
    subject = "You're in — CFB Sicko 2026"
    greeting = f"Hey {display_name},\n\n" if display_name else ""
    body = (
        f"{greeting}You're on the 2026 locks list.\n\n"
        "Five locks a week against frozen Tuesday lines. Window closes Thursday 6pm ET.\n"
        f"Lock your five: {app_url}/app\n\n"
        "There is no public signup. Use the email this was sent to.\n"
    )
    return subject, body


def slate_published_body(*, week_title: str, lock_at: str, app_url: str) -> tuple[str, str]:
    subject = f"{week_title} lines are up — five picks by Thursday 6pm ET"
    body = (
        f"{week_title} is open.\n\n"
        f"Submit exactly five picks against the published lines by {lock_at}.\n"
        f"Lock your five: {app_url}/app\n\n"
        "Use the listed numbers even if the market moves.\n"
    )
    return subject, body


def lock_reminder_body(*, week_title: str, lock_at: str, have: int, app_url: str) -> tuple[str, str]:
    return lock_warning_incomplete_body(week_title=week_title, lock_at=lock_at, have=have, app_url=app_url)


def lock_warning_incomplete_body(
    *, week_title: str, lock_at: str, have: int, app_url: str
) -> tuple[str, str]:
    subject = f"{week_title} locks in 1 hour — you have {have}/5"
    body = (
        f"You have {have}/5 picks in for {week_title}.\n"
        f"Window closes {lock_at}.\n"
        f"Lock your five: {app_url}/app\n"
    )
    return subject, body


def lock_warning_complete_body(*, week_title: str, lock_at: str, app_url: str) -> tuple[str, str]:
    subject = f"{week_title} window closes in 1 hour"
    body = (
        f"Your five are in for {week_title}. Last chance to change them.\n"
        f"Window closes {lock_at}.\n"
        f"Lock your five: {app_url}/app\n"
    )
    return subject, body


def lineup_saved_body(
    *,
    week_title: str,
    before: list[dict],
    after: list[dict],
    app_url: str,
) -> tuple[str, str]:
    subject = f"{week_title} lineup updated"
    lines = [f"Your {week_title} locks changed.", "", "Before:"]
    lines.extend(f"  {_pick_line(row)}" for row in before)
    lines.append("After:")
    lines.extend(f"  {_pick_line(row)}" for row in after)
    lines.append(f"\nLock your five: {app_url}/app")
    return subject, "\n".join(lines)


def line_moved_body(
    *,
    away: str,
    home: str,
    market: str,
    side: str,
    legal: float,
    market_line: float,
    app_url: str,
) -> tuple[str, str]:
    matchup = f"{away} / {home}"
    subject = f"Line moved: {matchup}"
    lock_label = "spread" if market == "spread" else "total"
    body = (
        f"{matchup} {lock_label} is now {market_line} "
        f"(your lock is still {legal}).\n"
        f"The listed Tuesday number is what grades.\n"
        f"Lock your five: {app_url}/app\n"
    )
    return subject, body


def _signed_spread(value: float) -> str:
    return f"{value:+g}" if value != 0 else "0"


def _pick_line(row: dict) -> str:
    if row.get("market") == "total":
        side = "Over" if row.get("side") == "over" else "Under"
        return f"{row.get('away')}/{row.get('home')} {side} {row.get('total')}"
    n = row.get("spread_home")
    if n is None:
        return f"{row.get('home') if row.get('side') == 'home' else row.get('away')}"
    if row.get("side") == "home":
        return f"{row.get('home')} {_signed_spread(float(n))}"
    return f"{row.get('away')} {_signed_spread(-float(n))}"


def standings_body(*, week_title: str, table_text: str, app_url: str) -> tuple[str, str]:
    subject = f"{week_title} standings"
    body = f"{week_title} is graded.\n\n{table_text}\n\nFull table: {app_url}/app/standings\n"
    return subject, body


def send_mail(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    bcc: list[str] | None = None,
    html: str | None = None,
) -> str:
    return get_sender().send(_message(to, subject, body, bcc=bcc, html=html))


def send_invite(to: str, *, display_name: str | None = None) -> str:
    subject, body = invite_body(display_name=display_name, app_url=Config.PUBLIC_APP_URL)
    return send_mail(to, subject, body)


def send_slate_published(to: str, *, week_title: str, lock_at: str) -> str:
    subject, body = slate_published_body(
        week_title=week_title, lock_at=lock_at, app_url=Config.PUBLIC_APP_URL
    )
    return send_mail(to, subject, body)


def send_lock_reminder(to: str, *, week_title: str, lock_at: str, have: int) -> str:
    subject, body = lock_reminder_body(
        week_title=week_title, lock_at=lock_at, have=have, app_url=Config.PUBLIC_APP_URL
    )
    return send_mail(to, subject, body)


def send_standings(to: str, *, week_title: str, table_text: str) -> str:
    subject, body = standings_body(
        week_title=week_title, table_text=table_text, app_url=Config.PUBLIC_APP_URL
    )
    return send_mail(to, subject, body)


def send_probe(to: str, *, kind: str = "slate") -> str:
    if kind == "reminder":
        return send_lock_reminder(to, week_title="Week 1", lock_at="Thursday 6pm ET", have=0)
    if kind == "standings":
        return send_standings(to, week_title="Week 1", table_text="1. Stu  5-0-0")
    return send_slate_published(to, week_title="Week 1", lock_at="Thursday 6pm ET")
