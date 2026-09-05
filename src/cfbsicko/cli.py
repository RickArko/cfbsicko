"""cfbsicko CLI — serve, migrate, import-sheet, mail probes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cfbsicko.config import Config
from cfbsicko.db import connect
from cfbsicko.import_sheet import UnmappedPicksError, import_master_sheet
from cfbsicko.seed_csv import SeedConflictError, extract_sheet_to_csv, games_exist, seed_from_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfbsicko")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    sub = parser.add_subparsers(dest="cmd")

    migrate = sub.add_parser("migrate", help="Apply SQLite migrations")
    migrate.add_argument("--db-path", default=None)

    imp = sub.add_parser("import-sheet", help="Import the master xlsx")
    imp.add_argument("xlsx")
    imp.add_argument("--db-path", default=None)
    imp.add_argument("--season", type=int, default=Config.SEASON)

    extract = sub.add_parser("extract-sheet", help="xlsx → CSV seed (laptop only)")
    extract.add_argument("xlsx")
    extract.add_argument("--out", required=True)
    extract.add_argument("--season", type=int, default=Config.SEASON)

    seed = sub.add_parser("seed-csv", help="Load a CSV seed directory into SQLite")
    seed.add_argument("seed_dir")
    seed.add_argument("--db-path", default=None)
    seed.add_argument(
        "--if-empty",
        action="store_true",
        help="No-op when the database already has games",
    )
    seed.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing week's games/picks (destructive; backup first)",
    )

    replay = sub.add_parser(
        "replay-week1",
        help="Replay Week 1 wide picks through save_picks (local SQLite only)",
    )
    replay.add_argument("--db-path", default=None)
    replay.add_argument("--seed-dir", default="seeds/2026/week-01")
    replay.add_argument(
        "--force",
        action="store_true",
        help="Replace existing Week 1 picks (destructive; a .pre-replay backup is written first)",
    )

    week2 = sub.add_parser(
        "publish-week2",
        help="Publish the local Week 2 rehearsal slate (does not touch Week 1 picks)",
    )
    week2.add_argument("--db-path", default=None)
    week2.add_argument("--seed-dir", default="seeds/2026/week-02")

    sub.add_parser("serve", help="Run the API (default)")

    mail = sub.add_parser("mail-probe", help="Send a one-off SMTP probe")
    mail.add_argument("to")
    mail.add_argument("--kind", default="slate", choices=["slate", "reminder", "standings"])

    invite = sub.add_parser(
        "invite-group",
        help="Print or send the single trial-league signup email",
    )
    invite.add_argument(
        "--review",
        action="store_true",
        help="Send the draft to the commissioner only",
    )
    invite.add_argument(
        "--blast",
        action="store_true",
        help="Send one email to the whole trial roster",
    )
    invite.add_argument(
        "--i-reviewed",
        action="store_true",
        help="Required with --blast so a review pass cannot be skipped",
    )
    invite.add_argument(
        "--to",
        default=None,
        help="Review inbox (default: first trial roster address)",
    )

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "migrate":
        path = Path(args.db_path).expanduser() if args.db_path else Config.database_path()
        conn = connect(path)
        conn.close()
        print(f"migrated {path}")
        return 0

    if cmd == "import-sheet":
        db_path = Path(args.db_path).expanduser() if args.db_path else Config.database_path()
        try:
            result = import_master_sheet(Path(args.xlsx), db_path, season=args.season)
        except UnmappedPicksError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(
            f"imported users={result.users} games={result.games} picks={result.picks} "
            f"empty={list(result.empty_players)}"
        )
        for warning in result.warnings:
            print(f"warning: {warning}")
        return 0

    if cmd == "extract-sheet":
        out = extract_sheet_to_csv(Path(args.xlsx), Path(args.out), season=args.season)
        print(f"extracted {out}")
        return 0

    if cmd == "seed-csv":
        db_path = Path(args.db_path).expanduser() if args.db_path else Config.database_path()
        if args.if_empty and games_exist(db_path):
            print(f"seed skipped (games already present) {db_path}")
            return 0
        try:
            result = seed_from_csv(Path(args.seed_dir), db_path, force=args.force)
        except SeedConflictError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(
            f"seeded users={result.users} games={result.games} picks={result.picks} "
            f"empty={list(result.empty_players)}"
        )
        return 0

    if cmd == "replay-week1":
        from cfbsicko.replay import ReplayPathError, replay_week1

        db_path = Path(args.db_path).expanduser() if args.db_path else Config.database_path()
        try:
            result = replay_week1(Path(args.seed_dir), db_path, force=args.force)
        except (ReplayPathError, SeedConflictError) as exc:
            print(exc, file=sys.stderr)
            return 2
        print(
            f"replayed users={result.users} picks={result.picks} "
            f"status={result.week_status} backup={result.backup}"
        )
        return 0

    if cmd == "publish-week2":
        from cfbsicko.replay import ReplayPathError, publish_week2_rehearsal
        from cfbsicko.store import SlateConflictError

        db_path = Path(args.db_path).expanduser() if args.db_path else Config.database_path()
        try:
            week = publish_week2_rehearsal(Path(args.seed_dir), db_path)
        except (ReplayPathError, SlateConflictError, FileNotFoundError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"published week={week['week_no']} status={week['status']} title={week['title']}")
        return 0

    if cmd == "mail-probe":
        from cfbsicko.mail import send_probe

        send_probe(args.to, kind=args.kind)
        print("sent")
        return 0

    if cmd == "invite-group":
        from cfbsicko.mail import group_invite_body, group_invite_review_body, send_mail
        from cfbsicko.trial_roster import trial_emails

        try:
            recipients = trial_emails()
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 2
        commish = (args.to or recipients[0]).strip().lower()
        app_url = Config.PUBLIC_APP_URL
        if "127.0.0.1" in app_url or "localhost" in app_url:
            app_url = "https://cfbsicko.com"

        if args.review and args.blast:
            print("use --review or --blast, not both", file=sys.stderr)
            return 2
        if args.blast and not args.i_reviewed:
            print("refusing --blast without --i-reviewed (send a --review first)", file=sys.stderr)
            return 2

        if args.review:
            subject, body = group_invite_review_body(app_url=app_url, recipients=recipients)
            try:
                send_mail(commish, subject, body)
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
                return 2
            print(f"review sent to {commish}")
            return 0
        if args.blast:
            subject, body = group_invite_body(app_url=app_url)
            bcc = [email for email in recipients if email != commish]
            try:
                send_mail(commish, subject, body, bcc=bcc)
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
                return 2
            print(f"blast sent to {len(recipients)} addresses")
            return 0

        subject, body = group_invite_review_body(app_url=app_url, recipients=recipients)
        print(f"To (review): {commish}")
        print(f"Subject: {subject}")
        print()
        print(body)
        return 0

    host = args.host or Config.HOST
    port = args.port or Config.PORT
    import uvicorn

    uvicorn.run("cfbsicko.app:app", host=host, port=port, factory=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
