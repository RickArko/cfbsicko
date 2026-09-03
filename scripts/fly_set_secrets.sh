#!/usr/bin/env bash
# Push secrets from .env. PUBLIC_APP_URL is forced to the Makefile/CLI value
# so a leftover localhost in .env cannot ship to Fly.
set -euo pipefail

_CLI_FLY_PUBLIC_APP_URL="${FLY_PUBLIC_APP_URL-}"
FLY_APP="${FLY_APP:-cfbsicko}"
ENV_FILE="${ENV_FILE:-.env}"
FLY_BIN="${FLY_BIN:-}"

if [[ -z "${FLY_BIN}" ]]; then
  if command -v fly >/dev/null 2>&1; then
    FLY_BIN="fly"
  elif command -v flyctl >/dev/null 2>&1; then
    FLY_BIN="flyctl"
  elif [[ -x "${HOME}/.fly/bin/fly" ]]; then
    FLY_BIN="${HOME}/.fly/bin/fly"
  else
    printf 'flyctl was not found.\n' >&2
    exit 127
  fi
fi

load_env_file() {
  local file="$1" line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ -n "${!key+x}" ]]; then
      continue
    fi
    if [[ "${value}" =~ ^\".*\"$ || "${value}" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    else
      value="${value%%#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    export "${key}=${value}"
  done < "${file}"
}

if [[ -f "${ENV_FILE}" ]]; then
  load_env_file "${ENV_FILE}"
fi

PUBLIC_APP_URL="${_CLI_FLY_PUBLIC_APP_URL:-https://cfbsicko.com}"
DATABASE_PATH="/data/locks.db"

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_PUBLISHABLE_KEY:-}${SUPABASE_ANON_KEY:-}" ]]; then
  printf 'SUPABASE_URL and a publishable/anon key are required in %s\n' "${ENV_FILE}" >&2
  exit 1
fi
if [[ "${COMMISH_ALLOWED_EMAILS:-}" == "*" ]]; then
  printf 'COMMISH_ALLOWED_EMAILS must never be *\n' >&2
  exit 1
fi

printf 'Setting Fly secrets for %s\n' "${FLY_APP}"
printf '  PUBLIC_APP_URL=%s\n' "${PUBLIC_APP_URL}"
printf '  DATABASE_PATH=%s\n' "${DATABASE_PATH}"

args=(
  WEB_AUTH_ENABLED=true
  DATABASE_PATH="${DATABASE_PATH}"
  PUBLIC_APP_URL="${PUBLIC_APP_URL}"
  SUPABASE_URL="${SUPABASE_URL}"
  SUPABASE_JWT_AUDIENCE="${SUPABASE_JWT_AUDIENCE:-authenticated}"
  CFBSICKO_SEASON="${CFBSICKO_SEASON:-2026}"
)
if [[ -n "${SUPABASE_PUBLISHABLE_KEY:-}" ]]; then
  args+=(SUPABASE_PUBLISHABLE_KEY="${SUPABASE_PUBLISHABLE_KEY}")
fi
if [[ -n "${SUPABASE_ANON_KEY:-}" ]]; then
  args+=(SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY}")
fi
if [[ -n "${SUPABASE_JWKS_URL:-}" ]]; then
  args+=(SUPABASE_JWKS_URL="${SUPABASE_JWKS_URL}")
fi
if [[ -n "${SUPABASE_JWT_SECRET:-}" ]]; then
  args+=(SUPABASE_JWT_SECRET="${SUPABASE_JWT_SECRET}")
fi
if [[ -n "${COMMISH_ALLOWED_EMAILS:-}" ]]; then
  args+=(COMMISH_ALLOWED_EMAILS="${COMMISH_ALLOWED_EMAILS}")
fi
if [[ -n "${CFBSICKO_TRIAL_ROSTER:-}" ]]; then
  args+=(CFBSICKO_TRIAL_ROSTER="${CFBSICKO_TRIAL_ROSTER}")
fi
if [[ -n "${SMTP_HOST:-}" && -n "${SMTP_FROM:-}" ]]; then
  args+=(
    SMTP_HOST="${SMTP_HOST}"
    SMTP_PORT="${SMTP_PORT:-465}"
    SMTP_FROM="${SMTP_FROM}"
    SMTP_USER="${SMTP_USER:-resend}"
  )
  if [[ -n "${SMTP_PASSWORD:-}" ]]; then
    args+=(SMTP_PASSWORD="${SMTP_PASSWORD}")
  fi
fi

"${FLY_BIN}" secrets set --app "${FLY_APP}" "${args[@]}"
