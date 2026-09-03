# CFB Sicko

Private college-football locks league for [cfbsicko.com](https://cfbsicko.com). Replaces the shared Google Sheet: frozen Tuesday lines, five picks by Thursday 6pm ET, running W-T-L standings.

## Local

```bash
make bootstrap
make test
# Import Week 1 from the exported sheet (uses ~/.cfbsicko/locks.db):
uv run cfbsicko import-sheet "data/assets/CFB Locks MASTER SHEET 2026.xlsx"
make run
```

Copy `.env.example` → `.env` and fill the dedicated Supabase project keys. Never point `DATABASE_PATH` at `~/.cfb_data/cfb.db`.

Week 1 import maps Scout’s `ILL/UAB Over 57.5` onto the frozen Illinois/UAB total **54.5** and prints a warning. The published line wins.

## Deploy

See [docs/deployment/first-deploy-setup.md](docs/deployment/first-deploy-setup.md). Fly app `cfbsicko`, one Machine, volume `cfbsicko_data`, canonical host `https://cfbsicko.com`.

## League rules

- $75 buy-in. Bottom 3 owe an extra $75 to the top 3 (one each).
- Payout of the pot: 60 / 30 / 10.
- Exactly five picks per week, any mix of spreads and totals, against the published line.
- Regular season only. FBS vs FCS included. No CCG, no Army-Navy.
