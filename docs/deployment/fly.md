# Fly.io — cfbsicko

See [first-deploy-setup.md](first-deploy-setup.md) for the full walkthrough.
See [transactional-email.md](transactional-email.md) for Resend.
See [backup-dr.md](backup-dr.md) for the SQLite file.

## Locked topology

- App `cfbsicko`, region `iad`
- One Machine, volume `cfbsicko_data` → `/data/locks.db`
- `auto_stop_machines = "off"` — a cold start on Thursday lock is unacceptable
- Never `fly scale count 2`
- No Redis, no worker, no warehouse-push

## Commands

```bash
make fly.app
make fly.volume
make fly.secrets
make fly.deploy
make fly.status
make fly.logs
make fly.certs
make fly.db-backup
```

`PUBLIC_APP_URL` on Fly is always `https://cfbsicko.com`. `.env` localhost cannot win.
