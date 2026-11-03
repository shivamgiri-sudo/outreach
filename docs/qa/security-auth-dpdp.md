# QA report: security, roles, rate limits, DPDP

Branch `qa/security-auth-dpdp`. Tests: `tests/qa/test_security-auth-dpdp.py` (tokens, roles, unauthenticated access,
secrets), `tests/qa/test_security-auth-dpdp_web.py` (webhooks, rate limits, unsubscribe links, XSS, CSV injection,
SSRF, full-width regression) and `tests/qa/test_security-auth-dpdp_dpdp.py` (export/erase, re-import, send channels,
restore, consent ledger, encryption at rest). All the fixture data is made up and lives in
`tests/qa/fixtures/security-auth-dpdp/`. No test makes a network call: sockets, DNS and provider transports are all faked.

## Method

- **Routes are read from the running app.** FastAPI 0.162 adds routers lazily (`_IncludedRouter`), so the tests walk
  `original_router.routes` to list all ~163 method/path pairs. A route added later is covered without editing a test.
- **Role matrix.** A viewer is sent every non-GET `/api` route and must get 403 (logout is the only exception). A
  caller is sent every route whose `needed_role` is owner and must get 403. Every `/api` route except
  `/api/login` and `/api/auth/status` must answer 401 when the caller sends no token, an empty Bearer, Basic auth or an
  empty webhook secret.
- **Time travel.** A token is renewed every 7 h for three days using the `now=` parameter in `auth`. The Calendly
  signature replay window is checked from 1 minute to 3 days. The 60-day grace for legacy unsubscribe links is checked
  at days 1, 30, 59, 61 and 400.
- **One realistic person across four sources.** Kavya Iyer (fictional) appears with different spellings in each file:
  `+91 90000 10042`, `090000 10042`, `+91-90000-10042`, and her email in mixed case.

## Bugs found and fixed (each fix in its own commit with a regression test)

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | High | **Callers and viewers could download CSV/JSON exports** of `tasks`, `activity` (email addresses, message bodies), `meetings` (attendee emails, join links) and `linkedin_queue`. README and PRD both say callers get "no CSV exports". The role guard never covered `/api/export/*`, and `exports.OWNER_ONLY` covered only some datasets. | Added `/api/export/` to `OWNER_PREFIXES` in `app/routes_auth.py`. `DownloadButton` is now hidden for non-owners. The catalogue `GET /api/export` stays readable. |
| 2 | High (DPDP) | **Restoring an older backup brought erased people back.** It also forgot unsubscribes made after the backup, so a "no" turned back into a "yes". `backup.restore` replaced `suppressed_hashes` and `suppressed` wholesale. | `restore` now merges both lists with the current ones and runs `guard_leads` on the restored leads (`app/backup.py`). |
| 3 | High (DPDP) | **Flows could SMS or email an erased contact** that another lead still held. `flow_actions.blocked_reason` checked only the plain suppression list, not the hashed erasure list. WhatsApp, the sequence runner and invites already did that check. | `blocked_reason` now also calls `privacy.is_blocked` (`app/flow_actions.py`). |
| 4 | Medium | **Full-width characters broke the importers.** One value such as `＠SUM(A1)` (a known formula-injection spelling) or `a＠b.example` made `urlparse` raise ValueError under NFKC normalisation. In the column sniffer (`standardise.host_of`) the whole quick import was refused with a cryptic 400. In `hygiene.company_domain` it crashed with a 500. | Both functions now treat such a value as having no host. |

## Verified as working (no change needed)

- **Tokens:** these are all refused: a tampered body or signature, the `v1` prefix, an alg-none JWT, four token parts,
  a non-JSON body, `exp` sent as a string, non-ASCII, oversized tokens, a token in the query string, a token dated
  10 minutes in the future (skew limit is 60 s), and a validly signed token for an unknown subject, `local`, or with a
  wrong password fingerprint.
- **Sessions:** logout revokes the whole renewal chain but leaves other sign-ins alone, and it never returns a renewed
  token. A password change signs out only that person. Demoting, promoting or removing a user applies to their live
  tokens at once. Users cannot escalate through their own record.
- **No-password installs** refuse remote callers, and also local requests that carry `X-Forwarded-For`, `Forwarded` or
  `X-Real-IP`, or a public `Host` header.
- **Login:** an unknown email and a wrong password give the same 401 body, so emails cannot be enumerated. Oversized
  input gets 400.
- **Path tricks** (`/API/leads`, `//api/leads`, `%6c`, `;x=1`, trailing slash and similar) never return data without a
  token.
- **Webhooks:** near-miss tokens are refused: prefix, suffix, case-flipped, leading space, reversed, empty, `%00` and
  Devanagari bytes. Path variants are refused too. A body over 5 MB and a non-numeric Content-Length are refused.
  - With the Meta app secret set, the token alone is not enough. Uppercase hex, a missing prefix, `sha1=`, the wrong
    body and the wrong key are all refused.
  - Calendly signatures older than 300 s, or dated in the future, are refused.
  - Secrets are compared with `hmac.compare_digest`; a spy in the tests checks this.
  - The activation event secret rejects the same near-misses. A secret sent in the query string does not work.
- **Rate limits:** login allows 5 a minute, `/u/` 30, `/demo/` 60 and webhooks 120.
  - A remote caller that changes `X-Forwarded-For` or `X-Real-IP` on every request still hits 429 at the limit.
  - Behind the local proxy, only the last hop counts. A garbage last hop falls back to the proxy address, so it does
    not get a fresh bucket.
  - `/api/login?x=1` counts against the same bucket. `/api/login/` and `/API/login` never issue a token.
  - Five wrong passwords lock the address even if the right password comes next.
- **Signed unsubscribe links:** these all give 404 and leave the lead untouched: another lead's signature, a truncated,
  extended or uppercased signature, an extra `.x` part, a signature made with an empty or wrong secret, an uppercased
  lead id, and the bare id with no outgoing email. The legacy window holds only for real (non-practice) emails sent
  before the cutover, for at most 60 days.
- **XSS:** 20 hostile payloads were imported (script, img onerror, svg, `</title>`, attribute breakout, `javascript:`,
  template syntax). They are escaped on every demo page and unsubscribe page. The demo page's only script is the
  nonce'd beacon, and the CSP is `script-src 'nonce-…'` on demo pages and `script-src 'none'` on unsubscribe pages.
  Markup inside a `/u/` or `/demo/` token gives an inert 404.
- **CSV formula injection:** no cell in any export dataset, or in the Apollo export, starts with `= + - @ \t \r`. The
  hostile payloads were confirmed to reach the files (each prefixed with `'`).
- **SSRF:** all of these are refused before any connection is opened:
  - loopback spellings: `127.1`, `2130706433`, `0x7f000001`, `0177.0.0.1`, `0`
  - IPv6 forms that point inside: `[::ffff:169.254.169.254]`, `[::ffff:7f00:1]`, `[::1]`, `fe80::1%eth0`, NAT64
    `64:ff9b::a9fe:a9fe`, 6to4 `2002:7f00:1::`, `fd00::1`
  - private names: `LOCALHOST.`, `metadata.google.internal`, `*.local`, `10.x:8080`
  - DNS answers that are mixed or rebinding, and empty answers
  - through `/api/scrape/extract`: `file://`, `gopher://` and `user:pw@` URLs (generic error messages)
- **Secrets:** every GET route was called as owner with secrets seeded everywhere the app stores them (API keys, OAuth
  tokens, IMAP password, custom headers, webhook tokens, the server secret, password hashes). No response contained a
  raw secret or `password_hash`.
  - With `OUTREACH_SECRET_KEY` set, keys are encrypted on disk, read back correctly after a restart, and left out of
    a default backup.
- **DPDP:**
  - **Export and erase:** the export holds everything on the person. After erasure, `store.json` contains neither her
    email, her number nor the consent text.
  - **Re-import:** she does not come back through any import path: quick paste, smart import (Apollo; Google Maps with
    BOM and `;`), team sheet, Contact Scraper JSON, a pasted web page, jobs CSV, referral, and the odd spellings
    `919000010042` and `090000 10042`.
  - **Sending:** WhatsApp, the email sequence, flows and invites all refuse a surviving duplicate that holds her
    details.
  - **Consent ledger:** it only grows. Erasure redacts her rows in place (hashed id, no text, original time) and leaves
    every other row byte-identical. The only routes that touch consent are `POST /api/leads/{id}/consent` and the
    owner-only `GET /api/compliance/consent-ledger`.

## Product limitations and recommendations (not changed)

1. **Callers can create, edit, delete and run campaigns, and change `/api/linkedin/settings`.** The PRD says callers
   get "no ... campaign rules ... sending runs" and "no settings". The README's list leaves campaigns out, and the UI
   shows campaigns to callers. *Recommendation:* the owner should decide. If the PRD is right, add `/api/campaigns`
   (writes) and `PUT /api/linkedin/settings` to the owner write rules in `routes_auth`.
2. **Erasure deletes only the selected lead.** If another lead holds the same person (for example, an old employer
   with her mobile but a different email), that record keeps her name and number. Sending to it is now blocked, but
   the data is still stored. *Recommendation:* when erasing, list the other leads that share a hashed contact and offer
   to erase or strip them too.
3. **A person with no email and no phone** (name and LinkedIn only) leaves no hash, so they can be imported again.
   *Recommendation:* hash the normalised LinkedIn URL too.
4. **Restore dry-run counts** do not yet subtract the erased leads that the confirmed restore will drop. Cosmetic.
5. **The `Authorization` scheme is case-sensitive:** `bearer x` is refused. This is harmless (it fails closed), but
   RFC 7235 says the scheme is case-insensitive.
6. **The CSV `'` prefix follows OWASP but is not bulletproof in Excel.** OWASP notes that the quote prefix can be lost
   if a file is saved and reopened in Excel. Full-width `＝` is not prefixed; Excel does not evaluate it, but other
   spreadsheet apps may differ. *Recommendation:* keep the prefix, and add a note in the export help text.
7. **A negative Content-Length** passes the webhook gate's pre-check. The body is still read and capped at 5 MB, so
   nothing is exposed.

## Formats and sources

- CSV injection rules (`= + - @ \t \r`, quote prefix and its Excel caveat): https://owasp.org/www-community/attacks/CSV_Injection
  and the OWASP testing guide: https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Input_Validation_Testing/21-Testing_for_CSV_Injection
- Calendly `Calendly-Webhook-Signature: t=…,v1=…` (HMAC-SHA256 of `t.` + raw body, with a tolerance window):
  https://developer.calendly.com/api-docs/overview/webhooks/webhook-signatures
- Meta WhatsApp Cloud API `X-Hub-Signature-256: sha256=<hex HMAC of the raw body keyed with the app secret>`:
  https://developers.facebook.com/docs/graph-api/webhooks/getting-started (summarised in
  https://hookdeck.com/webhooks/platforms/guide-to-whatsapp-webhooks-features-and-best-practices)
- Apollo contact CSV export (column set is configurable; the fixture uses the common default headers):
  https://knowledge.apollo.io/hc/en-us/articles/4409237712141-Export-Contacts-to-a-CSV
- The Google Maps scrape (semicolon-separated, BOM, comma decimals) and the team-sheet layout copy the shapes the
  app's own importers handle (`app/header_map.py`). The Contact Scraper JSON copies the shape the app's provider
  importer accepts (`app/routes_providers.py`).
