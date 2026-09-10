# Transactional email (Resend + `cfbsicko.com`)

Product SMTP (slate published, lock reminder, Sunday standings) uses `SMTP_*` on Fly.
Supabase Auth (magic link / confirm / reset) is a **different** path. Until Auth SMTP
points at Resend, Auth uses Supabase’s built-in mailer: **2 emails per hour**. That is
a launch blocker for invite week.

## Product mail

1. Resend → Domains → Add `cfbsicko.com`.
2. Add the records Resend shows. They belong on a `send.` subdomain so they do **not**
   replace the Namecheap A/AAAA/CNAME that point at Fly.

Typical shape:

- MX / TXT on `send` (SPF)
- TXT `resend._domainkey` (DKIM)

Grey-cloud / DNS-only if you later move DNS to Cloudflare. At Namecheap, just paste the records.

3. Verify. Then:

```text
SMTP_FROM=CFB Sicko <locks@cfbsicko.com>
SMTP_HOST=smtp.resend.com
SMTP_PORT=465
SMTP_USER=resend
SMTP_PASSWORD=<Resend API key>
```

`make fly.secrets` copies these when present. Probe:

```bash
uv run cfbsicko mail-probe you@example.com --kind slate
```

From host must be `cfbsicko.com`. Delivery `smtp`.

## Auth SMTP (required)

1. Supabase → Authentication → SMTP Settings. Enable custom SMTP.
2. Sender name `CFB Sicko`, sender `locks@cfbsicko.com`, host `smtp.resend.com`, port `465`, user `resend`, password = same API key.
3. Raise Auth emails/hour off the 2/hour default (300 is fine for twelve people).
   This is **Authentication → Rate Limits**, not the SMTP form. Enabling custom
   SMTP only bumps the cap to **30**. Turning SMTP off **resets it to 2**.
   Persist it with a personal access token in `.env` as `SUPABASE_ACCESS_TOKEN`
   (https://supabase.com/dashboard/account/tokens), then:

   ```bash
   make supabase.auth-smtp            # read; fail if cap < 300 or SMTP off
   make supabase.auth-smtp ENFORCE=1  # PATCH rate_limit_email_sent=300
   ```

   `make supabase.check` runs the same read after the project health probe.
4. Proton-safe Auth templates. Subject: `Your CFB Sicko code`. Body from
   [`supabase-magic-link.html`](supabase-magic-link.html).
   **Code only — do not include `{{ .ConfirmationURL }}`.** ProtonMail
   prefetches that URL and burns the six digits (`otp_expired`) before you
   open the message. Apply the same HTML to Magic Link, Confirm signup,
   Invite, and Reset password.

   ```text
   Your code: {{ .Token }}

   Type these six digits on cfbsicko.com. There is no sign-in button in this email.
   ```

   `make supabase.auth-smtp ENFORCE=1` pushes those templates when
   `SUPABASE_ACCESS_TOKEN` is set. The app already wants the digits typed in.
5. One password-reset / magic-link probe. Link host must be `https://cfbsicko.com`.

Until this is done, use `make fly.test-login` (see [auth-prod.md](../../.ai/plans/auth-prod.md)).

Do not buy a higher Supabase plan for this — it does not lift the built-in 2/hour mailer.
