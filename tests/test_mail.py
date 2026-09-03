from email.message import EmailMessage

from cfbsicko.mail import (
    lock_reminder_body,
    send_lock_reminder,
    send_slate_published,
    send_standings,
    set_sender_factory,
    slate_published_body,
    standings_body,
)


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
        _rem_s, rem_b = lock_reminder_body(
            week_title="Week 1", lock_at="Thu 6pm", have=2, app_url="https://cfbsicko.com"
        )
        assert "2/5" in rem_b
        _st_s, st_b = standings_body(
            week_title="Week 1", table_text="1. Stu  4-0-1", app_url="https://cfbsicko.com"
        )
        assert "4-0-1" in st_b

        assert send_slate_published("a@example.com", week_title="Week 1", lock_at="Thu 6pm") == "smtp"
        assert send_lock_reminder("a@example.com", week_title="Week 1", lock_at="Thu 6pm", have=1) == "smtp"
        assert send_standings("a@example.com", week_title="Week 1", table_text="1. Stu") == "smtp"
        assert len(sender.messages) == 3
        assert sender.messages[0]["To"] == "a@example.com"
        assert "Week 1" in sender.messages[0]["Subject"]
    finally:
        set_sender_factory(None)
