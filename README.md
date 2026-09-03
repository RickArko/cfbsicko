# CFB Sicko

Private college-football locks league for [cfbsicko.com](https://cfbsicko.com). Replaces the shared Google Sheet: frozen Tuesday lines, five picks by Thursday 6pm ET, running W-T-L standings.

## Local

```bash
make bootstrap
make test
# Week 1 CSVs (what Fly uses). xlsx stays on the laptop:
make seed-csv
# Optional: rebuild CSVs from a local sheet export
# make extract-sheet
make run
```

Copy `.env.example` → `.env` and fill the dedicated Supabase project keys. Never point `DATABASE_PATH` at `~/.cfb_data/cfb.db`.

The sheet had Scout’s `ILL/UAB Over 57.5`; the frozen Illinois/UAB total is **54.5**. Extract rewrites the stored pick to `UAB/Illinois Over 54.5` so the board and the grader use the same number.

## Deploy

See [docs/deployment/first-deploy-setup.md](docs/deployment/first-deploy-setup.md). Fly app `cfbsicko`, one Machine, volume `cfbsicko_data`, canonical host `https://cfbsicko.com`.

## League rules

- $75 buy-in. Bottom 3 owe an extra $75 to the top 3 (one each).
- Payout of the pot: 60 / 30 / 10.
- Exactly five picks per week, any mix of spreads and totals, against the published line.
- Regular season only. FBS vs FCS included. No CCG, no Army-Navy.
