"""Trial-league blast list. Real addresses live in env or a local file, never git."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROSTER_FILE = Path.home() / ".cfbsicko" / "trial_roster.txt"


def _parse_roster(raw: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("#"):
            continue
        if "|" in chunk:
            email, name = chunk.split("|", 1)
        else:
            email, name = chunk, ""
        email = email.strip().lower()
        name = name.strip()
        if not email or "@" not in email:
            raise ValueError(f"bad trial roster entry: {chunk!r}")
        rows.append((email, name or email.split("@", 1)[0]))
    return rows


def trial_roster() -> list[tuple[str, str]]:
    raw = (os.environ.get("CFBSICKO_TRIAL_ROSTER") or "").strip()
    if raw:
        return _parse_roster(raw)
    file_raw = os.environ.get("CFBSICKO_TRIAL_ROSTER_FILE")
    path = Path(file_raw).expanduser() if file_raw else _DEFAULT_ROSTER_FILE
    if path.is_file():
        return _parse_roster(path.read_text(encoding="utf-8"))
    return []


def trial_emails() -> list[str]:
    roster = trial_roster()
    if not roster:
        raise RuntimeError(
            "CFBSICKO_TRIAL_ROSTER is empty. Set email|Name,email|Name "
            "or a file via CFBSICKO_TRIAL_ROSTER_FILE."
        )
    return [email for email, _name in roster]
