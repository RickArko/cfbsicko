# Backup and disaster recovery — Fly SQLite

League picks, invites, and scores live only on Fly `/data/locks.db`. Auth is Supabase.
This file is irreplaceable.

## Copies

- Live: Fly volume `cfbsicko_data`
- Hot: `make fly.db-backup` → `~/.cfbsicko/backups/cfbsicko-YYYYMMDD-HHMMSS.db`
- Verify: `make fly.db-backup-verify` (`integrity_check=ok`, eligible filename)

## Restore (destructive)

```bash
make fly.db-backup-verify BACKUP=~/.cfbsicko/backups/cfbsicko-YYYYMMDD-HHMMSS.db
make fly.db-restore BACKUP=~/.cfbsicko/backups/cfbsicko-YYYYMMDD-HHMMSS.db \
  CONFIRM=1 CONFIRM_PROD=cfbsicko
```

The script refuses unless both confirm flags match. It copies the live file to
`/data/locks.db.prev` first.

Never restore a laptop `~/.cfb_data/cfb.db`.
