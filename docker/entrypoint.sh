#!/bin/sh
set -eu

ensure_data_ownership() {
  if [ "$(id -u)" != "0" ]; then
    return
  fi
  mkdir -p /data
  chown app:app /data
  db_path="${DATABASE_PATH:-/data/locks.db}"
  for f in "$db_path" "$db_path-wal" "$db_path-shm" "$db_path-journal"; do
    if [ -e "$f" ]; then
      chown app:app "$f"
    fi
  done
}

if [ "${1:-}" = "cfbsicko" ]; then
  ensure_data_ownership
  if [ "$(id -u)" = "0" ]; then
    gosu app sh -ec '
      export DATABASE_PATH="${DATABASE_PATH:-/data/locks.db}"
      echo "Migrating ${DATABASE_PATH}..."
      cfbsicko migrate --db-path "${DATABASE_PATH}"
      if [ -f /app/seeds/2026/week-01/games.csv ]; then
        cfbsicko seed-csv /app/seeds/2026/week-01 --db-path "${DATABASE_PATH}" --if-empty
      fi
    '
  else
    export DATABASE_PATH="${DATABASE_PATH:-/data/locks.db}"
    cfbsicko migrate --db-path "${DATABASE_PATH}"
    if [ -f /app/seeds/2026/week-01/games.csv ]; then
      cfbsicko seed-csv /app/seeds/2026/week-01 --db-path "${DATABASE_PATH}" --if-empty
    fi
  fi
fi

if [ "$(id -u)" = "0" ]; then
  ensure_data_ownership
  exec gosu app "$@"
fi

exec "$@"
