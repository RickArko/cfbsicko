"""2026 trial league — emails from the Sept 1 group thread."""

from __future__ import annotations

# Commissioner is first. Display names match the master sheet.
TRIAL_ROSTER: list[tuple[str, str]] = [
    ("rickarko@pm.me", "Rick"),
    ("stuartmfeeley@gmail.com", "Stu"),
    ("jm25feeley@gmail.com", "Jack"),
    ("bfee618@gmail.com", "Billy"),
    ("mmfeeley391@gmail.com", "Mike"),
    ("wtiebout10@gmail.com", "Wil"),
    ("scoutspalding@gmail.com", "Scout"),
    ("kenny.sherick@gmail.com", "Kenny"),
    ("owenkleppe711@gmail.com", "Owen"),
    ("lukerkleppe@gmail.com", "Luke"),
    ("joedegeeter@aol.com", "Joe"),
    ("rdegeeter5@gmail.com", "Rob"),
]


def trial_emails() -> list[str]:
    return [email for email, _name in TRIAL_ROSTER]
