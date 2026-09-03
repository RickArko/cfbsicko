# W7 — Weekly lines from CSV extract (not the sheet on Fly)

**Status:** planned 2026-09-03. Week 1 is a committed CSV seed (`seeds/2026/week-01/`). Later weeks follow the same shape.

**Authority:** live code, tests, `AGENTS.md`, and this file beat speculation. Do not re-open [Locked](#locked).

**Do this after** auth mail works enough to sign in as commish ([auth-prod.md](auth-prod.md)). Week 1 data can be loaded before that.

---

## Locked

- Fly never sees the Google Sheet or an `.xlsx`. Laptop (or a later export job) turns lines into CSV. The Machine loads CSV into `/data/locks.db`.
- Frozen commissioner lines. Tuesday publish is the only legal number. No live odds, no line-movement tracker, no `cfb-data` pin.
- No worker process, no Redis, no `fly scale count 2`. Sync is pull-on-command (`make fly.seed-csv` or an admin upload), not a daemon.
- SQLite only. Never `~/.cfb_data/cfb.db`.
- Exactly five structured picks against the frozen line. Republishing a week that already has picks is refuse-by-default.
- CSV is the wire format. Parquet is an optional later archive (S3/laptop), not a Fly runtime dependency.

---

## Why CSV (not Parquet, not xlsx)

Week 1 is 91 games + 50 picks. Stdlib `csv` is enough. Parquet needs `pyarrow` on the image for no gain. The xlsx parser (`openpyxl` + team aliases) stays a **laptop** tool: `cfbsicko extract-sheet`.

Committed layout:

```text
seeds/2026/week-01/week.csv      season,week_no,title,lock_at,status
seeds/2026/week-01/games.csv     sort_order,day_label,away,home,spread_home,total
seeds/2026/week-01/players.csv   display_name
seeds/2026/week-01/picks.csv     display_name,slot,away,home,market,side,raw_text
```

`spread_home` is already home-perspective. Picks are already mapped (`market`/`side`). Fly does not re-parse free text.

---

## Current vs next

| Now | W7 |
| --- | --- |
| Commish pastes the Tuesday email into `/app/admin` | Same paste **or** upload/replace `games.csv` |
| Week 1 came from a local xlsx import | Week 1 lives in `seeds/2026/week-01/` |
| `publish_slate` deletes all picks for that week | Seed/publish refuses if any pick rows exist unless `--force` |
| No week-2+ extract | `seeds/2026/week-NN/` each Tuesday |

---

## Workstreams (one PR each)

### W7.1 — Seed guard + week-N folders

- `seed_from_csv` takes `--week` from `week.csv` (already). Add `--force` (default off): if `SELECT COUNT(*) FROM picks WHERE week_id=?` > 0, exit 2.
- Apply the same guard to `publish_slate` (today it deletes picks — that is the bug this workstream closes).
- Tests: seed twice is idempotent when picks are the seed’s own rewrite *only* under `--force`; without `--force`, second seed no-ops or errors if picks exist.
- Acceptance: `make seed-csv SEED_DIR=seeds/2026/week-01` is safe on a DB that already has Week 1 picks.

### W7.2 — Tuesday lines extract (laptop)

- Extend `extract-sheet` (or add `extract-slate`) so a **lines-only** CSV can be built from:
  1. the existing email-format paste (no xlsx), and/or
  2. a local xlsx tab `WEEK N LINES` if the commish still writes the sheet on a laptop.
- Output: `seeds/2026/week-NN/week.csv` + `games.csv` only. No `picks.csv` until someone has picks.
- Default `lock_at`: that week’s Thursday 18:00 America/New_York (override in `week.csv`).
- Acceptance: given last year’s email body as a fixture, `games.csv` row count and `spread_home` signs match `parse_slate`.

### W7.3 — Load week N onto Fly without a code deploy

- `make fly.seed-csv SEED_DIR=seeds/2026/week-02` uploads the four CSVs to `/data/seeds/2026/week-02` and runs `cfbsicko seed-csv` (already sketched in `scripts/fly_seed_csv.sh`).
- Do **not** bake every future week into the Docker image. Week 1 in the image is a bootstrap only. Volume copy is the source of truth after first load.
- Acceptance: Week 2 appears on `/app` after the make target; image id unchanged.

### W7.4 — Commish UI: CSV upload + freeze badge

- `/app/admin`: file input for `games.csv` (and optional `week.csv`). Calls `POST /api/admin/weeks/{n}/games.csv`.
- Show frozen vs draft: after first successful publish, lines are read-only until `--force` (confirm modal: “this week already has N picks”).
- Keep the paste-email box; both paths hit the same store function.
- Browser-verify: upload Week 2 fixture, lock clock still Thursday 6pm ET, pick form lists new games.

### W7.5 — Optional later (do not start until 7.1–7.4 are green)

- Laptop cron / GitHub Action: “it’s Tuesday → extract → `fly.seed-csv`”. Still not a Fly worker.
- Thin CFBD (or similar) **scores** client for Sunday grade — scores, not lines.
- Parquet dump of the season under `~/.cfbsicko/archive/` for your own analytics. Never required on the Machine.

---

## How an agent should run W7.n

```text
Read .ai/plans/weekly-lines.md and AGENTS.md.
Task: Implement workstream W7.n.
Deliverable: code + tests for that workstream’s acceptance line.
Do not implement W7.n+1 in the same PR.
Do not add Google Sheets API, pyarrow, Redis, or a second Machine.
```

---

## Done when

- [ ] Week 1 CSV seed is what Fly uses (no xlsx on the volume)
- [ ] Tuesday Week N is `extract` → `seeds/2026/week-NN/` → `make fly.seed-csv`
- [ ] Republish will not silently wipe picks
- [ ] Paste-email and CSV upload produce the same `games` rows
- [ ] `make test` green; `/app` shows the new slate before lock
