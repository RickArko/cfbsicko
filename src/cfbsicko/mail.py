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
    return SmtpSender(
        host=Config.SMTP_HOST,
        port=Config.SMTP_PORT,
        user=Config.SMTP_USER,
        password=Config.SMTP_PASSWORD,
        use_ssl=Config.SMTP_USE_SSL,
        use_tls=Config.SMTP_USE_TLS,
    )


def _message(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    bcc: list[str] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = Config.SMTP_FROM
    recipients = [to] if isinstance(to, str) else [part.strip() for part in to if part.strip()]
    msg["To"] = ", ".join(recipients)
    if bcc:
        msg["Bcc"] = ", ".join(part.strip() for part in bcc if part.strip())
    msg["Subject"] = subject
    msg.set_content(body)
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
    subject = f"Reminder: {week_title} picks lock at {lock_at}"
    body = f"You have {have}/5 picks in for {week_title}.\nLock is {lock_at}. Lock your five: {app_url}/app\n"
    return subject, body


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
) -> str:
    return get_sender().send(_message(to, subject, body, bcc=bcc))


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
