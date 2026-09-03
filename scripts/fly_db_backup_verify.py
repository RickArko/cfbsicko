#!/usr/bin/env python3
"""Verify a dated cfbsicko-YYYYMMDD-HHMMSS.db backup."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

NAME = re.compile(r"^cfbsicko-\d{8}-\d{6}\.db$")


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1]:
        latest = sorted(Path.home().joinpath(".cfbsicko/backups").glob("cfbsicko-*.db"))
        if not latest:
            print("no backups", file=sys.stderr)
            return 2
        path = latest[-1]
    else:
        path = Path(sys.argv[1]).expanduser()
    if not NAME.match(path.name):
        print(f"ineligible name: {path.name}", file=sys.stderr)
        return 2
    if path.stat().st_size < 4096:
        print("file too small", file=sys.stderr)
        return 2
    conn = sqlite3.connect(path)
    try:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            print(f"integrity_check={check}", file=sys.stderr)
            return 2
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"ok {path} users={users}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
