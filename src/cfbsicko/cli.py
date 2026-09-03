"""cfbsicko CLI — serve, migrate, import-sheet, mail probes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cfbsicko.config import Config
from cfbsicko.db import connect
from cfbsicko.import_sheet import UnmappedPicksError, import_master_sheet
from cfbsicko.seed_csv import extract_sheet_to_csv, seed_from_csv


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

    sub.add_parser("serve", help="Run the API (default)")

    mail = sub.add_parser("mail-probe", help="Send a one-off SMTP probe")
    mail.add_argument("to")
    mail.add_argument("--kind", default="slate", choices=["slate", "reminder", "standings"])

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
        result = seed_from_csv(Path(args.seed_dir), db_path)
        print(
            f"seeded users={result.users} games={result.games} picks={result.picks} "
            f"empty={list(result.empty_players)}"
        )
        return 0

    if cmd == "mail-probe":
        from cfbsicko.mail import send_probe

        send_probe(args.to, kind=args.kind)
        print("sent")
        return 0

    host = args.host or Config.HOST
    port = args.port or Config.PORT
    import uvicorn

    uvicorn.run("cfbsicko.app:app", host=host, port=port, factory=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
