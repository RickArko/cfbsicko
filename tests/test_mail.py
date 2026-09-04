from email.message import EmailMessage

import pytest

from cfbsicko.mail import (
    group_invite_body,
    group_invite_review_body,
    invite_body,
    lock_reminder_body,
    send_invite,
    send_lock_reminder,
    send_mail,
    send_slate_published,
    send_standings,
    set_sender_factory,
    slate_published_body,
    standings_body,
)
from cfbsicko.trial_roster import trial_emails, trial_roster


class RecordingSender:
    def __init__(self):
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str:
        self.messages.append(message)
        return "smtp"


def test_templates_and_mocked_send():
    sender = RecordingSender()
    set_sender_factory(lambda: sender)
    try:
        sub, body = slate_published_body(
            week_title="Week 1", lock_at="Thu 6pm", app_url="https://cfbsicko.com"
        )
        assert "five picks" in sub.lower() or "picks" in body.lower()
        assert "Lock your five" in body
        assert "sheet" not in body.lower()
        inv_s, inv_b = invite_body(display_name="Stu", app_url="https://cfbsicko.com")
        assert "You're in" in inv_s
        assert "Lock your five" in inv_b
        assert "https://cfbsicko.com/app" in inv_b
        grp_s, grp_b = group_invite_body(app_url="https://cfbsicko.com")
        assert "Lock your five" in grp_s
        assert "https://cfbsicko.com/app" in grp_b
        assert "ProtonMail" in grp_b
        assert "sheet" in grp_b.lower()
        rev_s, rev_b = group_invite_review_body(
            app_url="https://cfbsicko.com", recipients=["a@example.com", "b@example.com"]
        )
        assert rev_s.startswith("[review]")
        assert "REVIEW ONLY" in rev_b
        assert "a@example.com" in rev_b
        _rem_s, rem_b = lock_reminder_body(
            week_title="Week 1", lock_at="Thu 6pm", have=2, app_url="https://cfbsicko.com"
        )
        assert "2/5" in rem_b
        assert "Lock your five" in rem_b
        _st_s, st_b = standings_body(
            week_title="Week 1", table_text="1. Stu  4-0-1", app_url="https://cfbsicko.com"
        )
        assert "4-0-1" in st_b

        assert send_invite("a@example.com", display_name="Stu") == "smtp"
        assert send_slate_published("a@example.com", week_title="Week 1", lock_at="Thu 6pm") == "smtp"
        assert send_lock_reminder("a@example.com", week_title="Week 1", lock_at="Thu 6pm", have=1) == "smtp"
        assert send_standings("a@example.com", week_title="Week 1", table_text="1. Stu") == "smtp"
        assert len(sender.messages) == 4
        assert sender.messages[0]["To"] == "a@example.com"
        assert "You're in" in sender.messages[0]["Subject"]
        assert "Week 1" in sender.messages[1]["Subject"]
    finally:
        set_sender_factory(None)


def test_trial_roster_from_env(monkeypatch):
    monkeypatch.setenv("CFBSICKO_TRIAL_ROSTER", "commish@example.com|Rick,player@example.com|Stu")
    monkeypatch.delenv("CFBSICKO_TRIAL_ROSTER_FILE", raising=False)
    assert trial_roster() == [("commish@example.com", "Rick"), ("player@example.com", "Stu")]
    assert trial_emails() == ["commish@example.com", "player@example.com"]


def test_trial_roster_empty_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("CFBSICKO_TRIAL_ROSTER", raising=False)
    monkeypatch.setenv("CFBSICKO_TRIAL_ROSTER_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(RuntimeError, match="empty"):
        trial_emails()


def test_group_blast_uses_bcc():
    sender = RecordingSender()
    set_sender_factory(lambda: sender)
    try:
        assert send_mail("commish@example.com", "blast", "body", bcc=["player@example.com"]) == "smtp"
        blast = sender.messages[0]
        assert blast["To"] == "commish@example.com"
        assert blast["Bcc"] == "player@example.com"
        assert "player@example.com" not in (blast["To"] or "")
    finally:
        set_sender_factory(None)
