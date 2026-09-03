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
4. One password-reset / magic-link probe. Link host must be `https://cfbsicko.com`.
5. Edit the Magic Link template so the 6-digit `{{ .Token }}` is first. ProtonMail consumes the link (`otp_expired`); users type the code.

Until this is done, use `make fly.test-login` (see [auth-prod.md](../../.ai/plans/auth-prod.md)).

Do not buy a higher Supabase plan for this — it does not lift the built-in 2/hour mailer.
