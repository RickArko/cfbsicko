# Production auth — temporary password, then real email codes

**Status:** in progress 2026-09-03. Frontend is live (`/api/health` → `"frontend": true`). Login is not.

**Do this in order.** The password workaround is only so you can click around Fly while Supabase mail is fixed. Turn it off before inviting the league.

---

## Why login is broken (three separate faults)

1. **Supabase Site URL is still localhost.** Magic-link / confirm redirects open `127.0.0.1`. That is a dashboard setting, not an app bug. `emailRedirectTo` in the Vue client is already `window.location.origin + "/app"`.
2. **Auth mail is still Supabase’s built-in sender (2 emails/hour).** “Send code” looks dead after the first couple of tries. Proton users also often never see the message.
3. **ProtonMail prefetches the magic link.** The one-time token is consumed (`otp_expired`) before you click. The 6-digit code in the same email is the real path — if the email arrives.

A fourth, independent issue: **this Mac still resolves `cfbsicko.com` to Namecheap parking `192.64.119.182`**, not Fly `66.241.125.155`. Until that is gone, test on `https://cfbsicko.fly.dev`. Certs for the custom domain are already Issued.

---

## Phase 0 — temporary test user (now)

Code gate: password login stays **off** on Fly unless both `TEST_PASS` and `ALLOW_TEST_LOGIN=true` are set. `make fly.secrets` still does **not** copy the password.

```bash
make fly.deploy
make fly.test-login
curl -s https://cfbsicko.fly.dev/api/auth/config
```

Expect `"local_login": true` and `"test_email"` set. Open `https://cfbsicko.fly.dev/`, sign in with the `.env` `TEST_EMAIL` / `TEST_PASS`. That mints a local JWT (`iss=cfbsicko-local`), auto-invites that email, and makes them commish if they are in `COMMISH_ALLOWED_EMAILS` (or via the test-email append).

When real codes work:

```bash
make fly.test-login-off
```

Treat the password as a shared secret on a public URL. Do not mail it to the league.

---

## Phase 1 — DNS so the apex is actually Fly

Namecheap → Advanced DNS for `cfbsicko.com`:

- Remove **URL Redirect**, **Parking**, and any leftover `@` A to `192.64.119.182`.
- Apex `@`: A → `66.241.125.155`, AAAA → `2a09:8280:1::182:4698:0` (confirm with `fly ips list -a cfbsicko`).
- `www`: CNAME → `cfbsicko.fly.dev` **or** the same A/AAAA. Do not mix a Redirect + A on the same host.

Then:

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
dig +short A cfbsicko.com @8.8.8.8
# must be 66.241.125.155, not 192.64.119.182
curl -s https://cfbsicko.com/api/health
```

`www` already 301s to `https://cfbsicko.com`. Until the apex answers, use fly.dev.

---

## Phase 2 — Supabase URL configuration (fixes localhost redirects)

Dashboard → Authentication → URL Configuration:

| Setting | Value |
| --- | --- |
| Site URL | `https://cfbsicko.com` |
| Redirect URLs | `https://cfbsicko.com/**`, `https://www.cfbsicko.com/**`, `https://cfbsicko.fly.dev/**`, plus `http://127.0.0.1:8000/**` and `http://localhost:8000/**` if you still develop against this project |

Email → enable Email provider. Confirm / magic-link templates stay on.

If a link still opens `127.0.0.1`, Site URL was not saved. Do not “fix” it in Python.

---

## Phase 3 — Resend Auth SMTP (fixes “no code”)

Same as [docs/deployment/transactional-email.md](../../docs/deployment/transactional-email.md):

1. Verify `cfbsicko.com` in Resend (`send.` subdomain records — do not replace Fly A/AAAA).
2. Supabase → Authentication → SMTP: host `smtp.resend.com`, port `465`, user `resend`, password = Resend API key, from `locks@cfbsicko.com`.
3. Raise Auth emails/hour off `2` (300 is fine for twelve people).
4. Product SMTP on Fly (`SMTP_*`) is a different path (slate / reminder / standings). Both should use Resend.

Until this lands, “Send code” will 429 after two messages.

---

## Phase 4 — Proton-safe email template

Supabase → Authentication → Email Templates → Magic Link (and Invite if you use it).
Paste [`docs/deployment/supabase-magic-link.html`](../../docs/deployment/supabase-magic-link.html).

- Put the **6-digit `{{ .Token }}`** at the top in large type.
- First sentence: “Type this on cfbsicko.com. Do not tap the button if you use ProtonMail.”
- Keep `{{ .ConfirmationURL }}` below for Gmail/Apple users. Proton prefetches the link and burns it (`otp_expired`).

The Vue client already calls `signInWithOtp` + `verifyOtp({ type: "email" })`. No app change required once mail arrives.

Optional later: drop implicit hash flow for PKCE (`flowType: "pkce"`). Not blocking.

---

## Phase 5 — Seed prod SQLite and invite the 12

Empty `/data/locks.db` has no sheet users and no invites. After you can sign in as commish:

1. Import the master sheet onto the volume (or restore a local imported `locks.db` with `CONFIRM=1 CONFIRM_PROD=cfbsicko`). Never upload `~/.cfb_data/cfb.db`.
2. From `/app/admin`, invite each real email and set `display_name` to the sheet name (Stu, Jack, …) so existing pick rows attach.
3. Smoke: invited Gmail gets a code, types it, sees `/app`, saves 5 picks. Proton user types the code, never clicks the link.

```bash
curl -s https://cfbsicko.fly.dev/api/health
make fly.db-backup
```

---

## Done when

- [ ] `https://cfbsicko.fly.dev/` shows the password form only while `ALLOW_TEST_LOGIN` is on
- [ ] Apex `cfbsicko.com` resolves to Fly, not Namecheap parking
- [ ] A fresh Auth email opens `https://cfbsicko.com/app`, not localhost
- [ ] Gmail and Proton can both enter a 6-digit code
- [ ] `make fly.test-login-off` has been run
- [ ] Twelve invites exist; sheet display names are linked
