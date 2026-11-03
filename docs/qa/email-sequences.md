# QA: email sequences end to end

Branch `qa/email-sequences`. Tests: `tests/qa/test_email-sequences.py` (57 tests, about 100 s). Fixture:
`tests/qa/fixtures/email-sequences/leads_wizard_60.csv`.

## What was simulated

- **Leads.** 63 fictional rows exported the way Excel does it: UTF-8 BOM, ALL CAPS and Hinglish names, honorifics
  (Mr., Smt., Dr., Md., Shri), "Pvt. Ltd." / "PRIVATE LIMITED", phones written `+91 90000 10000` / `09000010001` /
  `+91-90000-10003` / two numbers in one cell, `www.` and `https://…/careers` websites, roles like
  `SR. EXEC - BPO (NIGHT)` and `#REQ-2231`, blank country cells, "india " with a trailing space, a blank row,
  three duplicate rows (different case), and three North-American leads (`Canada`, `United States`, `U.S.A.`). They
  are imported through the import wizard (`/api/import/commit`): 60 leads, 3 duplicates folded in. All are enrolled.
- **Clock.** Runs at 14 times a day (08:45, 09:00, then hourly to 17:40, 17:59, 18:00, 21:00 IST) for 30 days, from
  Tuesday 3 Nov 2026, through `run_due(store, now, …)`.
- **Providers (all fakes).** Resend through the classic sending-domain card, and the hub providers SendGrid, Postmark
  and SMTP. The fakes return each provider's success shape (Resend `{"id"}`, SendGrid `202` + `X-Message-Id`,
  Postmark `{"ErrorCode":0,"MessageID"}`) and raise the errors the real transports raise.

| Scenario | Checked |
|---|---|
| Practice for 30 days (SendGrid) | No provider calls. All 60 leads get all 4 practice emails. Only weekdays 09:00–17:59 IST. At most 5 a run and 20 a day. One per company domain a day. Steps 0/3/7/14 in order, each spaced from the previous email actually sent. `sent_days` stays empty and stage stays `new`. |
| Practice day, then live for 4 weeks (×4 providers) | Warm-up gives exactly 10 on the first live day and 20 in week 2, never more than 35. Every practised lead still gets a real Email 1. Run on day 6: a negative reply, a positive reply, an unsubscribe (GET page, then POST), a bounce and a suppressed address. Run on day 9: a complaint, which pauses sending (one complaint in 50+ sends is above 0.3%); it is resumed by hand the next day. Nothing is sent to any of these leads after the event. US/Canada leads are held. |
| Wire contents (×4) | Body = outbox body. Signed unsubscribe link in the body, the same link in `List-Unsubscribe` plus a mailto. `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. `Message-ID` = outbox `message_id` @ the sending domain. `Idempotency-Key` only for Resend, and `X-Idempotency-Key` never leaks as a header. Identity footer `company · website · contact email`. Keys and Message-IDs are unique per lead, step and mode. |
| Provider errors (4 providers × 401/429/500/timeout) | Sending pauses; the error does not count against the lead (no `in_flight`, no `failed_attempts`). A 401 (or SMTP login refused) waits for a person and is cleared by `POST /api/sending/resume`. 429, 5xx and timeouts resume after 1 h. The retry carries the **same idempotency key, Message-ID and body**. |
| Refused recipient (Resend 422, SendGrid 400, Postmark 422 / 200 with `ErrorCode 406`, SMTP 550 `SMTPRecipientsRefused`) | Only that lead fails; the run carries on. It backs off for 1 h, then is retried and sent. After 3 refusals it stops as `send_failed`. Resume restarts it; a second resume answers 400. |
| Caps | Per-run cap 7 and daily cap 12 send 7, then 5, then 0. The cap resets on the next IST day. Three leads at one company domain send one a day. |
| US/Canada | Held in live mode while no `sender_address` is set (spellings `U.S.A.`, `U.S.`, `usa`, `United States`, `Canada`, ` CA `). Sent once an address is set, with the address in the footer. |
| Practice → live, clear practice | Live sending starts at Email 1 for leads that practised Emails 1–2. `/api/practice/clear` removes only `dry_run` rows and the practice keys. A provider pause does not block practice runs. |
| Unsubscribe links | GET shows the page and changes nothing; POST unsubscribes. A tampered signature returns 404. An unsigned link for a lead with no pre-cutover email returns 404. Resume refuses an unsubscribed lead. |

## Bugs found and fixed

1. **US leads written "U.S.A." were emailed live without a postal address** (high, compliance). `needs_postal_address`
   compared the raw text, so `U.S.A.` and `U.S.` were not recognised. Punctuation is now ignored
   (`app/sequences.py`, one line). Commit 2273e92.
2. **A retry after a provider error was a different request under the same idempotency key** (high).
   - **Cause:** each attempt got a new random Message-ID, and Email 1 with the sample link also got a new demo token,
     because demo fields were only saved after a successful send.
   - **Effect on Resend:** Resend answers `409 invalid_idempotent_request` when a key comes back with a different
     payload. The engine classifies 409 as retryable, so it pauses again every hour. Once the key expires (24 h), a
     message the provider had in fact accepted before a timeout can be delivered twice.
   - **Fix:** the Message-ID is now derived from the idempotency key (uuid5), and the demo fields are saved when the
     send is claimed (`app/sequence_runner.py`). Commit 405d219.
3. **`POST /api/leads/{id}/sequence/resume` returned 200 for leads that replied or unsubscribed** (low). The 400
   message was unreachable because `store.update_lead` returns the unchanged lead. Nothing was restarted, but the UI
   reported success. `resume_lead` now returns None when it did not resume. Commit f66843c.

## Product limitations and recommendations (not changed)

- **Timeouts can duplicate on SendGrid, Postmark and SMTP.** These have no idempotency key, so a timeout after the
  provider accepted the message is retried an hour later and can arrive twice. Recommendation: for these providers,
  treat a timeout as `needs_check` (as for a crash), not as an automatic retry.
- **Resend behaviour after a failed attempt is undocumented.** Resend does not say what a reused key returns when the
  earlier attempt failed without sending (for example a 422 refused recipient). The per-lead retry reuses the key.
  It is harmless if Resend stores only successes; verify with the test-mode API.
- **Postmark's terms forbid cold email.** The catalog says so in a setup note, but Postmark can still be chosen as the
  cold-sequence provider and sends normally. Recommendation: block or warn when live sending is turned on with
  Postmark active.
- **Follow-ups are not threaded.** Steps 2–4 have their own subjects and no `In-Reply-To`/`References` to Email 1.
  Replies are still matched, through `In-Reply-To` against outbox Message-IDs. This is a deliberate copy choice;
  noted only.
- **The US/Canada hold does not apply in practice mode.** The practice outbox therefore shows emails to these leads,
  although live sending would hold them. Consider marking these rows "would be held in live".
- **A single complaint can stop all sending.** At 50 or more sends in 7 days, one complaint (above 0.3%) pauses all
  sending until a person resumes it. This is correct for sender reputation, and the Outbox page should make it
  obvious.

## Sources for formats

- Resend idempotency (24 h retention, 409 `invalid_idempotent_request` for a changed payload, 409
  `concurrent_idempotent_requests`): https://resend.com/docs/dashboard/emails/idempotency-keys (fetched for this QA)
- Resend send API and 422 `validation_error`: https://resend.com/docs/api-reference/emails/send-email,
  https://resend.com/docs/api-reference/errors
- SendGrid v3 Mail Send (202 and `X-Message-Id`, 400 error body): https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send
- Postmark email API, and error codes 300 (invalid email) and 406 (inactive recipient):
  https://postmarkapp.com/developer/api/email-api, https://postmarkapp.com/developer/api/overview#error-codes
- SMTP reply codes (421 try later, 550 user unknown): RFC 5321 §4.2, https://www.rfc-editor.org/rfc/rfc5321
- One-click unsubscribe headers: RFC 8058, https://www.rfc-editor.org/rfc/rfc8058
- Postal address in commercial email: CAN-SPAM (FTC guide), https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business,
  and CASL, https://crtc.gc.ca/eng/com500/faq500.htm
