#!/usr/bin/env python3
"""Verify Supabase Auth custom SMTP and the emails/hour cap.

The built-in mailer is 2/hour and cannot be raised. Custom SMTP is required
before ``rate_limit_email_sent`` sticks. Disabling SMTP resets the cap to 2.

Needs ``SUPABASE_ACCESS_TOKEN`` from https://supabase.com/dashboard/account/tokens
(not the anon/publishable key). Never prints the token or ``smtp_pass``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MANAGEMENT = "https://api.supabase.com/v1/projects"
TARGET_EMAILS_PER_HOUR = 300
EXPECTED_SMTP_HOST = "smtp.resend.com"
EXPECTED_FROM_HOST = "cfbsicko.com"
MAGIC_LINK_SUBJECT = "Your CFB Sicko code"
TEMPLATE_FIELDS = (
    "mailer_templates_magic_link_content",
    "mailer_templates_confirmation_content",
    "mailer_templates_invite_content",
    "mailer_templates_recovery_content",
)
SUBJECT_FIELDS = (
    "mailer_subjects_magic_link",
    "mailer_subjects_confirmation",
    "mailer_subjects_invite",
    "mailer_subjects_recovery",
)


def project_ref(supabase_url: str) -> str:
    host = urlparse(supabase_url).hostname or ""
    ref = host.split(".", 1)[0]
    if not ref or ref == "YOUR_PROJECT_REF":
        raise ValueError("SUPABASE_URL is missing a project ref")
    if "cfbfantasy" in host:
        raise ValueError("This looks like the cfbfantasy project. Use cfbsicko.")
    return ref


def auth_config_url(ref: str) -> str:
    return f"{MANAGEMENT}/{ref}/config/auth"


def fetch_auth_config(ref: str, token: str) -> dict[str, Any]:
    response = requests.get(
        auth_config_url(ref),
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if response.status_code == 401:
        raise RuntimeError("SUPABASE_ACCESS_TOKEN was rejected (401)")
    if response.status_code >= 400:
        raise RuntimeError(f"auth config HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("auth config response was not an object")
    return payload


def patch_email_rate(ref: str, token: str, emails_per_hour: int) -> dict[str, Any]:
    return patch_auth_config(ref, token, {"rate_limit_email_sent": emails_per_hour})


def magic_link_html() -> str:
    raw = (ROOT / "docs" / "deployment" / "supabase-magic-link.html").read_text(encoding="utf-8")
    body = raw
    if body.lstrip().startswith("<!--"):
        end = body.find("-->")
        if end != -1:
            body = body[end + 3 :]
    html = body.strip()
    if "{{ .Token }}" not in html:
        raise RuntimeError("supabase-magic-link.html is missing {{ .Token }}")
    if "{{ .ConfirmationURL }}" in html:
        raise RuntimeError("supabase-magic-link.html must not include {{ .ConfirmationURL }}")
    return html


def template_payload() -> dict[str, str]:
    html = magic_link_html()
    payload = {field: html for field in TEMPLATE_FIELDS}
    payload.update({field: MAGIC_LINK_SUBJECT for field in SUBJECT_FIELDS})
    return payload


def template_problems(config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in SUBJECT_FIELDS:
        subject = str(config.get(field) or "").strip()
        if subject != MAGIC_LINK_SUBJECT:
            issues.append(f"{field}={subject or 'default'!r} (want {MAGIC_LINK_SUBJECT!r})")
    for field in TEMPLATE_FIELDS:
        html = str(config.get(field) or "")
        if "{{ .Token }}" not in html:
            issues.append(f"{field} is missing {{{{ .Token }}}} (still the click-the-link mail)")
        if "{{ .ConfirmationURL }}" in html:
            issues.append(f"{field} still has {{{{ .ConfirmationURL }}}} (Proton burns it)")
    return issues


def patch_auth_config(ref: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(
        auth_config_url(ref),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"auth config patch HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("auth config patch response was not an object")
    return payload


def summarize(config: dict[str, Any]) -> dict[str, Any]:
    host = str(config.get("smtp_host") or "").strip().lower()
    admin = str(config.get("smtp_admin_email") or "").strip().lower()
    sender = str(config.get("smtp_sender_name") or "").strip()
    raw = config.get("rate_limit_email_sent")
    try:
        rate = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        rate = 0
    return {
        "smtp_host": host,
        "smtp_admin_email": admin,
        "smtp_sender_name": sender,
        "rate_limit_email_sent": rate,
        "custom_smtp": bool(host),
    }


def problems(summary: dict[str, Any], *, want: int) -> list[str]:
    issues: list[str] = []
    if not summary["custom_smtp"]:
        issues.append("custom SMTP is off — Auth is on the built-in 2/hour mailer")
    elif summary["smtp_host"] != EXPECTED_SMTP_HOST:
        issues.append(f"smtp_host is {summary['smtp_host']!r}, want {EXPECTED_SMTP_HOST}")
    admin = summary["smtp_admin_email"]
    if admin and not admin.endswith(f"@{EXPECTED_FROM_HOST}"):
        issues.append(f"smtp_admin_email is {admin!r}, want a {EXPECTED_FROM_HOST} address")
    if summary["rate_limit_email_sent"] < want:
        issues.append(f"rate_limit_email_sent={summary['rate_limit_email_sent']} (want >={want})")
    return issues


def report(summary: dict[str, Any]) -> None:
    smtp = "on" if summary["custom_smtp"] else "OFF (built-in 2/hour)"
    print(
        f"auth smtp={smtp} host={summary['smtp_host'] or '-'} "
        f"from={summary['smtp_admin_email'] or '-'} "
        f"emails_per_hour={summary['rate_limit_email_sent']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help=(
            f"PATCH rate_limit_email_sent to {TARGET_EMAILS_PER_HOUR} and "
            "push the Proton-safe code-only Auth templates"
        ),
    )
    parser.add_argument(
        "--min",
        type=int,
        default=TARGET_EMAILS_PER_HOUR,
        help=f"minimum emails/hour (default {TARGET_EMAILS_PER_HOUR})",
    )
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    token = (os.getenv("SUPABASE_ACCESS_TOKEN") or "").strip()
    if not url:
        print("SUPABASE_URL is required", file=sys.stderr)
        return 2
    if not token:
        print(
            "SUPABASE_ACCESS_TOKEN is empty. Create a personal access token at "
            "https://supabase.com/dashboard/account/tokens and put it in .env. "
            "This is not the publishable/anon key.",
            file=sys.stderr,
        )
        return 2
    try:
        ref = project_ref(url)
        config = fetch_auth_config(ref, token)
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    summary = summarize(config)
    report(summary)
    issues = problems(summary, want=args.min) + template_problems(config)

    if args.enforce:
        body: dict[str, Any] = {}
        if summary["rate_limit_email_sent"] < args.min:
            if not summary["custom_smtp"]:
                print(
                    "refusing --enforce: enable custom SMTP (Resend) first or the cap resets to 2",
                    file=sys.stderr,
                )
                return 2
            body["rate_limit_email_sent"] = args.min
        if template_problems(config):
            body.update(template_payload())
        if body:
            try:
                config = patch_auth_config(ref, token, body)
            except (RuntimeError, requests.RequestException) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            try:
                config = fetch_auth_config(ref, token)
            except (RuntimeError, requests.RequestException) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            summary = summarize(config)
            if "rate_limit_email_sent" in body:
                print(f"enforced emails_per_hour={summary['rate_limit_email_sent']}")
            if any(key in body for key in TEMPLATE_FIELDS):
                print("enforced Proton-safe Auth templates (code only, no confirm link)")
            report(summary)
            issues = problems(summary, want=args.min) + template_problems(config)

    if issues:
        for item in issues:
            print(item, file=sys.stderr)
        print(
            "Dashboard: Authentication → Rate Limits, SMTP, and Email Templates. "
            f"https://supabase.com/dashboard/project/{ref}/auth/templates",
            file=sys.stderr,
        )
        return 2
    print(f"auth smtp ok  project={ref} emails_per_hour={summary['rate_limit_email_sent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
