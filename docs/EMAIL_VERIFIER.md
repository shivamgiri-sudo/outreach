# Mailbox probe: check an email address without sending anything

The probe asks the recipient's own mail server whether a mailbox exists, then hangs up. No message is ever sent
(the `DATA` command is never issued). It is for checking a short list of business contacts, tens to low hundreds a day,
before one-to-one outreach. It is not for harvesting addresses or building lists to sell.

## How one address is checked
1. Reject bad syntax (`invalid_syntax`).
2. Look up the domain's MX records, best first. No MX: use the A record. Neither: `no_mail_server`.
3. Connect to the best MX on port 25 (10 s timeout): `EHLO <our hostname>`, `MAIL FROM:<sender at our domain>`, `RCPT TO:<address>`, note the
   reply, `QUIT`.
4. Catch-all test: `RCPT TO:<16 random characters>@domain` on the same server. If that is accepted too, the domain is `catch_all`
   and the answer for the real address means nothing.

| Status | Meaning |
|---|---|
| `confirmed` | The server accepted the real address and refused a made-up one. Strong evidence, not a guarantee of delivery. |
| `rejected` | The server refused with 550 / 551 / 553 (mailbox unknown). |
| `catch_all` | The server accepts everything. Not confirmed. |
| `unknown_blocked` | The server refused to talk, banned our IP, or returned a policy error (5.7.x, blocklist text). Not confirmed. |
| `unknown_temporary` | A 4xx reply (greylisting, rate limit). The CLI retries once after 5 minutes. Not confirmed. |
| `no_mail_server` | No MX and no A record. |
| `invalid_syntax` | The address is malformed. |
| `error` | Timeout or connection failure. |

Only `confirmed` and `rejected` are answers. Everything else is "not confirmed", never "invalid".

## What you need
- **Outbound port 25.** AWS, GCP, Azure, Oracle Cloud and most home or office broadband block it. Test with `telnet <any mx host> 25`.
  **The Oracle host this app runs on blocks it**, so the probe cannot run there. Run the command line tool from a machine where
  port 25 is open, or use a VPS provider that allows it.
- **A real hostname with matching reverse DNS (PTR)** on the server's IP, used as `--helo`.
- **A sender address at a domain you own** (`--mail-from`).
- **A static IP with a clean reputation.** Check it against the common blocklists first.

## Command line
```
pip install dnspython
python tools/verify_emails.py --input people.csv --output checked.csv \
    --helo mail.yourdomain.com --mail-from verify@yourdomain.com
```
The input needs an `email` column; other columns are kept. The output adds `status, smtp_code, smtp_message, mx_host, is_catch_all,
checked_at`, and a count per status is printed. `tools/samples/` has an example input and output.

Options: `--delay` (seconds between probes, never below 1), `--limit` (default 200, refused above 500), `--cache` (default
`verify_cache.json`, results reused for 7 days), `--log` (every probe: time, address, MX host, code, status), `--retry-wait`
(seconds before the one retry of greylisted answers, 0 to skip).

## Inside the app
Under Integrations → Lead data, the card **Mailbox probe (self-hosted, no email sent)** takes the hostname and sender address. Once
connected it joins the free verifiers: the built-in MX check runs first, then the probe, then any paid verifier. The Hiring engine and
Find & enrich use it automatically. If port 25 is blocked on the server, the card's Test button says so and enrichment skips it.

## Safeguards (fixed, not settings)
- At most 1 probe per second overall, and 1 connection at a time to the same MX host.
- At most 500 addresses per run (200 by default).
- Results cached for 7 days: the same address is never probed twice in a week.
- A domain is dropped for the rest of the run after two policy blocks in a row.
- Every probe is logged (the app writes `probe.log` next to its data file).
- No follow-up message of any kind.

## Known limits
- Google Workspace and Microsoft 365 often accept every address or give no answer, so many results are `unknown_blocked` or `catch_all`.
- Some servers delay the answer (greylisting) or accept first and bounce later.
- `confirmed` means the server accepted the recipient at that moment.
