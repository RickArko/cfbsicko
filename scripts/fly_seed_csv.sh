#!/usr/bin/env bash
# Upload a CSV seed directory and load it into /data/locks.db.
# Never uploads the xlsx.
set -euo pipefail

FLY_APP="${FLY_APP:-cfbsicko}"
FLY_BIN="${FLY_BIN:-fly}"
SEED_DIR="${SEED_DIR:-seeds/2026/week-01}"
REMOTE="/data/seeds/$(basename "$(dirname "${SEED_DIR}")")/$(basename "${SEED_DIR}")"

if [[ ! -f "${SEED_DIR}/games.csv" || ! -f "${SEED_DIR}/picks.csv" ]]; then
  printf 'SEED_DIR=%s must contain games.csv and picks.csv\n' "${SEED_DIR}" >&2
  exit 2
fi

"${FLY_BIN}" ssh console --app "${FLY_APP}" -C "mkdir -p ${REMOTE}"
for name in week.csv games.csv players.csv picks.csv; do
  "${FLY_BIN}" ssh sftp put --app "${FLY_APP}" "${SEED_DIR}/${name}" "${REMOTE}/${name}"
done
# Never pass --force here. A retry after lock/grade must not wipe live picks.
FORCE_FLAG=""
if [[ "${FORCE:-}" == "1" ]]; then
  FORCE_FLAG="--force"
fi
"${FLY_BIN}" ssh console --app "${FLY_APP}" -C \
  "cfbsicko seed-csv ${REMOTE} --db-path /data/locks.db ${FORCE_FLAG}"
