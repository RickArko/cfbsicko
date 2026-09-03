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
make bootstrap
make test
make run
```

Deploy harness: `docs/deployment/`. Do not add warehouse-push, Redis, or a worker process.
