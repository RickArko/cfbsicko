# First-time setup — Supabase, `.env`, Fly, Namecheap

Step-by-step for **cfbsicko**. Pattern mirrors the cfbfPy harness (dedicated Supabase project, one Fly Machine + volume, Resend, custom domain) without the fantasy warehouse.

## Prerequisites

- Python 3.13+, uv, Node 22 (frontend build)
- Fly CLI
- Dedicated Supabase project named `cfbsicko` (not `cfbfantasy`)
- Namecheap DNS for `cfbsicko.com`
- Resend account (product mail + Auth SMTP)

## 1. Clone and bootstrap

```bash
cd ~/ragit/cfbsicko
make bootstrap
```

Copies `.env.example` → `.env` if missing. Set `DATABASE_PATH=~/.cfbsicko/locks.db`. Never `~/.cfb_data/cfb.db`.

## 2. Create a Supabase project

Dashboard → New project → `cfbsicko`. Authentication → Providers → Email: enabled.

### `.env` keys

From Project Settings → API:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY` (or legacy anon)

### URL configuration (local)

| Setting | Value |
| --- | --- |
| Site URL | `http://127.0.0.1:8000` |
| Redirect URLs | `http://127.0.0.1:8000/**`, `http://localhost:8000/**`, `http://127.0.0.1:5173/**` |

Add production URLs in step 7.

```bash
make supabase.check
make run
```

Import Week 1 after the schema exists. Prefer the committed CSVs (no xlsx on Fly):

```bash
make seed-csv
# laptop only, if the sheet changed:
# make extract-sheet
```

On Fly, after a deploy that includes `cfbsicko seed-csv`:

```bash
make fly.seed-csv
```

## 3. Fly — one-time

```bash
fly auth login
make fly.app
make fly.volume
make fly.secrets
```

`make fly.secrets` **forces** `PUBLIC_APP_URL=https://cfbsicko.com` and `DATABASE_PATH=/data/locks.db`. A localhost value in `.env` cannot override that.

## 4. Deploy

```bash
cd frontend && npm ci && npm run build && cd ..
make fly.deploy
curl -s https://cfbsicko.fly.dev/api/health
```

No `GITHUB_TOKEN`. This image has no private sports dependency.

Boot runs `cfbsicko migrate` against `/data/locks.db`.

## 5. Namecheap + certs

```bash
make fly.certs
```

In Namecheap for `cfbsicko.com`:

- Apex: A / AAAA records Fly prints (`fly certs show`)
- `www` CNAME → `cfbsicko.fly.dev`

Wait until `fly certs check` is Ready. Document URLs on `www` and `cfbsicko.fly.dev` 301 to `https://cfbsicko.com`. Ops curls stay on `https://cfbsicko.fly.dev/api/health`.

## 5b. Temporary test login (only until Auth mail works)

Password login is off on Fly unless you opt in. After a deploy that includes `ALLOW_TEST_LOGIN`:

```bash
make fly.test-login
```

Sign in on `https://cfbsicko.fly.dev/` with `.env` `TEST_EMAIL` / `TEST_PASS`. Unset before inviting the league:

```bash
make fly.test-login-off
```

Full order (DNS parking, Site URL, Resend Auth SMTP, Proton codes): [`.ai/plans/auth-prod.md`](../../.ai/plans/auth-prod.md).

If `cfbsicko.com` hangs or shows a Namecheap parking page, this Mac is still resolving `@` to `192.64.119.182`. Remove URL Redirect / Parking records; keep only the Fly A/AAAA. Use fly.dev until `dig +short A cfbsicko.com` is `66.241.125.155`.

## 6. Supabase production URLs

| Setting | Value |
| --- | --- |
| Site URL | `https://cfbsicko.com` |
| Redirect URLs | `https://cfbsicko.com/**`, `https://www.cfbsicko.com/**`, `https://cfbsicko.fly.dev/**` plus localhost if you share the project |

If invite mail opens `127.0.0.1`, Site URL is still local. Do not "fix" it in code.

## 7. Auth SMTP (launch blocker)

Built-in Supabase mail is 2/hour. Point Auth SMTP at the same Resend account. See [transactional-email.md](transactional-email.md).

## 8. Seed prod

```bash
# after first deploy, from a trusted laptop:
fly ssh console -a cfbsicko
# or upload the imported local locks.db only when the volume is empty
```

Do **not** `sftp put` a fantasy `cfb.db`. Invite the twelve display names to real emails from `/app/admin`.

## 9. Smoke

1. Open https://cfbsicko.com
2. Sign in with an invited email
3. Save five picks (before Thursday 6pm ET)

```bash
curl -s https://cfbsicko.com/api/health
curl -sI https://www.cfbsicko.com
make fly.db-backup
make fly.db-backup-verify
```
