#!/usr/bin/env bash
set -euo pipefail

FLY_APP="${FLY_APP:-cfbsicko}"
FLY_BIN="${FLY_BIN:-fly}"
DEST_DIR="${HOME}/.cfbsicko/backups"
mkdir -p "${DEST_DIR}"
stamp="$(date -u +%Y%m%d-%H%M%S)"
dest="${DEST_DIR}/${FLY_APP}-${stamp}.db"

"${FLY_BIN}" ssh console --app "${FLY_APP}" -C \
  "sqlite3 /data/locks.db \".backup /data/locks.backup.db\""
"${FLY_BIN}" ssh sftp get --app "${FLY_APP}" /data/locks.backup.db "${dest}"
printf '%s\n' "${dest}"
