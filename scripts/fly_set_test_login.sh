#!/usr/bin/env bash
# Temporary Fly password login. Not part of make fly.secrets.
# Unset with: make fly.test-login-off
set -euo pipefail

FLY_APP="${FLY_APP:-cfbsicko}"
ENV_FILE="${ENV_FILE:-.env}"
FLY_BIN="${FLY_BIN:-}"
ACTION="${1:-on}"

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
  printf 'Unsetting ALLOW_TEST_LOGIN TEST_PASS TEST_EMAIL TEST_DISPLAY_NAME on %s\n' "${FLY_APP}"
  "${FLY_BIN}" secrets unset --app "${FLY_APP}" \
    ALLOW_TEST_LOGIN TEST_PASS TEST_EMAIL TEST_DISPLAY_NAME
  exit 0
fi

if [[ -f "${ENV_FILE}" ]]; then
  load_env_file "${ENV_FILE}"
fi

if [[ -z "${TEST_PASS:-}" ]]; then
  printf 'TEST_PASS is empty. Set it in %s or the environment.\n' "${ENV_FILE}" >&2
  exit 1
fi

printf 'WARNING: enabling temporary password login on public Fly app %s\n' "${FLY_APP}"
printf '  TEST_EMAIL=%s\n' "${TEST_EMAIL:-rickarko@pm.me}"
printf '  Unset with: make fly.test-login-off\n'

"${FLY_BIN}" secrets set --app "${FLY_APP}" \
  ALLOW_TEST_LOGIN=true \
  TEST_EMAIL="${TEST_EMAIL:-rickarko@pm.me}" \
  TEST_PASS="${TEST_PASS}" \
  TEST_DISPLAY_NAME="${TEST_DISPLAY_NAME:-Rick}"
