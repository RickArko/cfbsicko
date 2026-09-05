# Agents

This repo is the **CFB Sicko locks league** at `https://cfbsicko.com`.

**Authority:** live code, tests, this file, and `README.md` beat [`.ai/plans/build.md`](.ai/plans/build.md) when they disagree about current behavior. The plan beats speculation about next work.

## Locked

- Standalone app. Not a cfbfPy feature. No `cfb-data` git pin.
- SQLite only: local `~/.cfbsicko/locks.db`, Fly `/data/locks.db`. Never `~/.cfb_data/cfb.db`.
- Supabase Auth (dedicated project) + invite allowlist. Resend for product mail and Auth SMTP.
- One Fly Machine, volume `cfbsicko_data`, region `iad`. Never `fly scale count 2`.
- Exactly five structured picks. Frozen commissioner lines. Thursday 18:00 America/New_York lock unless overridden.
- Money is out of band (`buy_in_paid` flags only).

## Commands

```bash
make bootstrap   # uv sync + cp .env.example .env + npm install in frontend/
make run         # uv run cfbsicko serve  (FastAPI on HOST:PORT from .env)
make test        # uv run pytest
make lint        # ruff check + format --check on src tests scripts
make fmt         # ruff --fix + format
```

- Python 3.13, managed by `uv` only (no pip). Ruff line-length 110, E501 ignored.
- Single test: `uv run pytest tests/test_api.py::test_name`. Full suite always runs via pre-commit on commit.
- Frontend dev: `cd frontend && npm run dev` — Vite on :5173, proxies `/api` to `127.0.0.1:8000`. In prod (Docker/Fly) the API serves the built `frontend/dist` via `CFBSICKO_FRONTEND_DIST`; `dist/` is gitignored.
- CLI subcommands: `serve` (default), `migrate`, `import-sheet`, `extract-sheet` (laptop only — xlsx never leaves the laptop), `seed-csv`, `replay-week1`, `publish-week2`, `mail-probe`, `invite-group`.

## Testing gotchas

- Tests hard-require `data/assets/CFB Locks MASTER SHEET 2026.xlsx`, but `data/` is gitignored. A fresh clone fails until that xlsx is restored locally — this is expected, not a code bug.
- `tests/conftest.py` mints HS256 JWTs with a test secret; no real Supabase/SMTP is contacted. `conftest` sets env vars and `reload_config()` before imports — keep that ordering when editing it.
- The `clock` fixture pins 2026-09-03 17:59 ET (one minute before Thursday lock); most lock-behavior tests pivot on it.
- `imported` fixture loads the whole master sheet into a tmp SQLite db.

## Schema quirk

Migrations are not `.sql` files. `SCHEMA_SQL` in `src/cfbsicko/db.py` is checksum-gated: editing it without bumping `SCHEMA_VERSION` makes every boot fail with "schema_migrations checksum mismatch — refuse to boot". Sub-migrations live in `leagues.py` and `live.py`.

## Deploy / ops

- Deploy harness: `docs/deployment/`. Do not add warehouse-push, Redis, or a worker process.
- `make fly.secrets` never copies test-login vars; toggle those only with `make fly.test-login` / `fly.test-login-off`.
- `make fly.db-restore` requires `CONFIRM=` and `CONFIRM_PROD=` — it is deliberately hard to run by accident.
- Seed CSVs live in `seeds/2026/week-NN/`; `make seed-csv` (local) and `make fly.seed-csv` (on the Machine) load them. `SHEET`/`SEED_DIR` are overridable Make vars.
