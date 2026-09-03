#!/usr/bin/env bash
# Destructive. Requires CONFIRM=1 CONFIRM_PROD=cfbsicko
set -euo pipefail

FLY_APP="${FLY_APP:-cfbsicko}"
FLY_BIN="${FLY_BIN:-fly}"

if [[ "${CONFIRM:-}" != "1" || "${CONFIRM_PROD:-}" != "${FLY_APP}" ]]; then
  printf 'Refusing restore. Set CONFIRM=1 CONFIRM_PROD=%s BACKUP=path\n' "${FLY_APP}" >&2
  exit 2
fi
if [[ -z "${BACKUP:-}" || ! -f "${BACKUP}" ]]; then
  printf 'BACKUP must be an existing file\n' >&2
  exit 2
fi

"${FLY_BIN}" ssh sftp put --app "${FLY_APP}" "${BACKUP}" /data/locks.restore.db
"${FLY_BIN}" ssh console --app "${FLY_APP}" -C \
  "cp /data/locks.db /data/locks.db.prev && cp /data/locks.restore.db /data/locks.db"
printf 'restored %s onto %s\n' "${BACKUP}" "${FLY_APP}"
