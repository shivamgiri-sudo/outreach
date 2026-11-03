# QA: WhatsApp, SMS and calls

Branch `qa/whatsapp-sms-calls`. Tests: `tests/qa/test_whatsapp-sms-calls*.py` (64 tests). Shared helpers are in
`tests/qa/whatsapp_sms_calls_support.py` and fixtures in `tests/qa/fixtures/whatsapp-sms-calls/`. No test reaches the
network: provider calls go through `FakeHTTP`, DNS goes through a fake resolver, and the real transport is replaced by
one that fails the test if anything calls it.

## What was simulated

- **Leads:** a 123-row Apollo-style CSV with every common phone format (`+91…`, `+91 90000 1xxxx`, `0…`, `91-…`,
  `(+91) …`, bare 10 digits), ALL CAPS and honorific names, "Pvt. Ltd." and "Private Limited" company names, rows with
  no phone, and one duplicate. The result is 122 leads and 1 duplicate, with every phone normalised to `+91XXXXXXXXXX`.
- **Consent:**
  - With no consent, the send is refused and nothing is queued or sent.
  - A WhatsApp STOP withdraws consent, so the next send is refused.
  - A later automatic "haan" does not restore consent. The ledger records exactly one withdrawal.
- **Practice vs live:**
  - With no provider, the result is `dry_run`.
  - With a provider saved but `live_whatsapp` off, the result is still `dry_run`, and no HTTP call is made.
  - Real sends happen only when the provider is saved and `live_whatsapp` is on.
  - SMS uses `live_sms` the same way.
- **Template variables:** for all five WhatsApp providers, `{{1}}` is the cleaned first name ("Priyanka", not
  "SHARMA"), `{{2}}` is the sender, `{{3}}` is the cleaned company name (legal suffix dropped) and `{{4}}` is the demo link.
  Each request is checked against the provider's documented request shape (URL, auth header, number format).
- **Hinglish:** all four `_hinglish` templates render with no leftover `{{…}}` and end with "Reply STOP to opt out.".
- **24-hour window:**
  - An email "yes" records consent but does not open the window.
  - An inbound WhatsApp message opens it.
  - The window was checked after 23 h (open), after 24 h 1 min (closed) and on day 3 (closed). Templates still work outside it.
  - AiSensy refuses free text with a reason.
- **Inbound classification:** the Meta batch has 8 messages: text, the "Stop promotions" button, an interactive
  button, "STOP", "Band karo… mat bhejo", an image, a question, and an unknown number. Meta resending the same webhook is
  ignored, as are status callbacks (sent/delivered/read/failed). `unsupported` messages with `errors[]` go to a person.
  Gupshup, Interakt, WATI (text, button, list, own echo), AiSensy, Twilio SMS (form-encoded) and MSG91 inbound are
  covered too.
- **Closed leads stay closed:** a WhatsApp "yes" from a customer or a disqualified lead does not move the stage and
  does not record consent. A customer's yes creates a review task.
- **Webhook security:**
  - Meta GET verify echoes `hub.challenge` only when the token is right and the mode is `subscribe`.
  - A Meta POST is rejected without a valid `X-Hub-Signature-256`, even when `?token=` is correct.
  - Token-auth providers are rejected without the secret.
- **Provider errors:** 13 error shapes are covered:
  - Meta: 131026, 132001, 131047, and 190 (expired token).
  - WhatsApp providers: Gupshup `status:error`, Interakt `result:false`, WATI `result:false`, AiSensy `success:"false"`.
  - SMS providers: MSG91 `type:error`, Fast2SMS `return:false`, Twilio 21211 and 401.
  - Calls: Exotel 400.

  In each case the person sees a readable error, the outbox row is marked `failed` and the lead's stage stays the same.
- **SMS:**
  - MSG91 flow body: `template_id`, `short_url`, `recipients[{mobiles, var1..}]`.
  - Fast2SMS `route=dlt`: national number, pipe-joined variables.
  - Twilio form: `To`, `Body`, `From`. Text is required.
  - Invalid numbers are refused before any call is made.
  - The Integrations "send test" button works.
- **Calls:**
  - With no provider, the result is `dry_run`.
  - Click-to-call fills `{phone}`. A link saved on the hub reaches the fields that `CallDesk.jsx` reads
    (`telephony.mode == "api"`, `values.click_to_call_url`).
  - Exotel `Calls/connect.json` form: `From` agent, `To` lead, `CallerId`. The call Sid is parsed from the reply.
  - Region allow-list, invalid number and an Exotel error are covered.
- **Call desk loop:**
  - A WhatsApp yes at 19:00 IST on Friday gives Priya a call task due Monday at 11:00 IST.
  - A second yes over the weekend does not add a second task. The task shows on Today.
  - A plain note does not close the task; the call-desk outcome (`kind=call`) does.
  - `/activity type=call` closes it too, and works for a renamed caller ("Anita").
  - An invalid outcome is rejected.

## Bugs found and fixed (each in its own commit, each with a regression test)

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | High | Meta's marketing opt-out quick reply "Stop promotions", and Twilio's opt-out keywords STOPALL / END / QUIT, were classified as "other". The lead was not unsubscribed and got a generic task instead (a "no" read as "not a no"). | `app/replies.py`: matches `stop promotions?` anywhere, and STOPALL/END/QUIT only when they are the whole message. |
| 2 | High | WATI sends quick-reply taps as `buttonReply {id, title}` with no `text` field, and the parser dropped them. A "Stop promotions" tap on WATI was lost. List replies were dropped the same way. | `app/channels/inbound_parse.py`: falls back to `buttonReply`/`listReply` `text` or `title`. |
| 3 | Medium | Free-text WhatsApp messages (sent within the 24 h window) had no opt-out line. This breaks PRD 4.4 and the DPDP rule "opt-out in every message"; every template has one. | `app/whatsapp.py`: appends "Reply STOP to opt out." unless the person already wrote it. |
| 4 | Medium | `whatsapp_last_inbound_at` was set to the time the webhook was processed. Meta retries webhooks for days, so a message retried 30 h late reopened a window that had already closed. Meta then refuses free text (131047). | `app/inbound.py`: uses the provider's message timestamp when it is earlier than now. |

## Limitations and recommendations (not changed)

1. **No lead-facing SMS or bridged-call action.** `send_sms()` and `start_call()` are only reached from the
   Integrations "send test" button. No route sends an SMS to a lead or starts an Exotel call from the call desk, so SMS
   consent rules, the DLT promotional window (10:00–21:00) and DND checks have no code path to protect yet.
   *Recommendation:* when an SMS action is added, gate it like `app/whatsapp.send` (consent, closed stages, do-not-contact
   list, opt-out text) and check the promotional time window.
2. **WhatsApp delivery statuses are ignored.** Meta `statuses` (including `failed` with error 131026/131047) do not
   update `whatsapp_outbox`. *Recommendation:* record status per `provider_id`, the same way email `delivered` works.
3. **The same parameters go to every template.** All four variables are sent to every template, even ones whose body
   uses fewer (for example `nudge_not_published` has no demo link). A template approved on Meta with fewer
   placeholders is rejected with error 132000 (parameter count mismatch). *Recommendation:* register every template with
   all four placeholders as documented, or send only the placeholders the template uses.
4. **A WhatsApp/SMS STOP only closes the matched lead.** The phone number is put on the do-not-contact list only after
   an erasure (PRD 9.5 already notes this). If two lead records share one number (the same person at two companies), only the
   first match is unsubscribed. *Recommendation:* add a phone suppression list for channel opt-outs.
5. **Loose E.164 fallback.** `channels.phone.e164` accepts any `+<8–15 digits>`. A mistyped `+91` number with 11
   national digits, or one starting with 1–5, is passed to the provider. Lead phones are normalised at import, so this
   affects only numbers typed into the test button. *Recommendation:* for `+91` numbers, require exactly 10 national digits.
6. **Call slot for late-night yeses.** `next_call_slot` returns "now" for a weekday time before 18:30 IST, including
   02:00. The task is simply due at once, which is harmless, but a 09:30 floor would match calling hours better.
7. **Window start in tests.** Tests that patch `inbound._now` to a future date and send a payload with no timestamp
   (AiSensy, Twilio, MSG91) get the real current time as the message time. This is fine in production. Such tests should
   pass a timestamp.
8. **AiSensy and MSG91 inbound shapes are the least documented.** The parsers accept several field names. Confirm them
   against a real callback before going live.

## Sources for payload formats

- Meta WhatsApp Cloud API:
  - Send messages: https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates
  - Webhook payloads: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
  - Button replies: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/button/
  - Interactive replies: https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/interactive-reply-buttons-messages
  - Error codes: https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes
  - Verify request and `X-Hub-Signature-256`: https://developers.facebook.com/docs/graph-api/webhooks/getting-started
- "Stop promotions" marketing opt-out button. Seen in providers' help pages, not in Meta's own reference:
  - https://docs.kapso.ai/docs/whatsapp/templates/marketing-opt-outs
  - https://support.qiscus.com/hc/en-us/articles/13766924007577
  - https://help.sleekflow.io/en_US/whatsapp/guide-to-using-marketing-message-templates
- Gupshup:
  - Send text: https://docs.gupshup.io/docs/text
  - Inbound messages: https://docs.gupshup.io/docs/inbound-messages
  - Payload types: https://docs.gupshup.io/docs/types-of-payloads
- Interakt: https://interakt.shop/resource-center/how-to-send-whatsapp-templates-using-apis-webhooks
- WATI:
  - API docs: https://docs.wati.io/
  - Webhooks, including `buttonReply {id, title}` and `replyContextId`: https://support.wati.io/en/articles/11463225-how-to-track-template-message-delivery-and-message-status-using-wati-webhooks
- AiSensy: https://aisensy.com/docs/api-docs/api-reference-docs/
- MSG91 flow (DLT): https://control.msg91.com/apidoc/textsms/send-sms-flow.php
- Twilio SMS:
  - Incoming message webhook: https://www.twilio.com/docs/messaging/guides/webhook-request
  - Error 21211: https://www.twilio.com/docs/errors/21211
  - Opt-out keywords: https://help.twilio.com/articles/223134027
- Fast2SMS: https://docs.fast2sms.com/
- Exotel click-to-call (connect two numbers): https://developer.exotel.com/api/outgoing-call-to-connect-two-numbers

All fixture data is fictional: numbers in the 90000 1xxxx range, `*.example.*` and `*.invalid` domains, and made-up names and IDs.
