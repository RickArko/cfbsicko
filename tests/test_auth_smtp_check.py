import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_auth_smtp.py"
_SPEC = importlib.util.spec_from_file_location("check_auth_smtp", _PATH)
assert _SPEC and _SPEC.loader
check_auth_smtp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_auth_smtp)
problems = check_auth_smtp.problems
project_ref = check_auth_smtp.project_ref
summarize = check_auth_smtp.summarize


def test_project_ref_from_url():
    assert project_ref("https://fybycaqosqqewrtxppny.supabase.co") == "fybycaqosqqewrtxppny"


def test_summarize_built_in_mailer_is_two():
    summary = summarize({"rate_limit_email_sent": 2, "smtp_host": ""})
    assert summary["custom_smtp"] is False
    assert summary["rate_limit_email_sent"] == 2
    assert any("built-in" in item for item in problems(summary, want=300))


def test_summarize_custom_smtp_still_too_low():
    summary = summarize(
        {
            "smtp_host": "smtp.resend.com",
            "smtp_admin_email": "locks@cfbsicko.com",
            "rate_limit_email_sent": 30,
        }
    )
    assert summary["custom_smtp"] is True
    issues = problems(summary, want=300)
    assert any("rate_limit_email_sent=30" in item for item in issues)


def test_ok_at_target():
    summary = summarize(
        {
            "smtp_host": "smtp.resend.com",
            "smtp_admin_email": "locks@cfbsicko.com",
            "rate_limit_email_sent": 300,
        }
    )
    assert problems(summary, want=300) == []


def test_magic_link_html_is_code_only():
    html = check_auth_smtp.magic_link_html()
    assert "{{ .Token }}" in html
    assert "{{ .ConfirmationURL }}" not in html


def test_default_magic_link_template_is_rejected():
    issues = check_auth_smtp.template_problems(
        {
            "mailer_subjects_magic_link": "Your sign-in link",
            "mailer_templates_magic_link_content": '<p><a href="{{ .ConfirmationURL }}">Sign in</a></p>',
        }
    )
    assert any("sign-in link" in item for item in issues)
    assert any("ConfirmationURL" in item for item in issues)
