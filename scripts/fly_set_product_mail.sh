#!/usr/bin/env bash
# Hold or restore league SMTP on Fly. Not part of make fly.secrets password copy
# when PRODUCT_MAIL is off. Auth OTP is Supabase and is unchanged.
set -euo pipefail

FLY_APP="${FLY_APP:-cfbsicko}"
ENV_FILE="${ENV_FILE:-.env}"
FLY_BIN="${FLY_BIN:-}"
ACTION="${1:-off}"

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

if [[ "${ACTION}" == "off" ]]; then
  printf 'Holding product mail on %s (Auth OTP unchanged)\n' "${FLY_APP}"
  "${FLY_BIN}" secrets unset --app "${FLY_APP}" SMTP_PASSWORD || true
  "${FLY_BIN}" secrets set --app "${FLY_APP}" PRODUCT_MAIL=off
  exit 0
fi

if [[ "${ACTION}" != "on" ]]; then
  printf 'usage: %s on|off\n' "$0" >&2
  exit 2
fi

if [[ -f "${ENV_FILE}" ]]; then
  load_env_file "${ENV_FILE}"
fi

if [[ -z "${SMTP_PASSWORD:-}" ]]; then
  printf 'SMTP_PASSWORD is empty. Set it in %s before turning mail on.\n' "${ENV_FILE}" >&2
  exit 1
fi

printf 'WARNING: enabling league SMTP on public Fly app %s\n' "${FLY_APP}"
printf '  Unset with: make fly.mail-off\n'

"${FLY_BIN}" secrets set --app "${FLY_APP}" \
  PRODUCT_MAIL=on \
  SMTP_PASSWORD="${SMTP_PASSWORD}"
