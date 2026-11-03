# Lead capture (inbound leads)

MCN Outreach can catch leads the moment they arrive from an ad, a form or another tool, put them in the same lead list as
everything else and show where each one came from. Open **Integrations, Lead capture** (owner only) and press **Add connector**.

What happens to every captured lead, whichever connector sent it:

1. The details are tidied the same way as an import (name, company, e-mail in lower case, phone in +91 form, city).
2. A person on the do-not-contact list, or whose data was erased, is **refused**. The sender gets `200 OK` (so the platform does
   not retry for ever) and the capture log says `refused: suppressed/erased`.
3. A repeat is ignored: the same platform lead id, the same `Idempotency-Key`, or the same details within ten minutes.
4. The lead is **never dropped as a duplicate**. If the e-mail, phone or company domain and person already exist, the lead is merged
   (empty fields are filled, nothing is overwritten), the source is added to its history and a timeline line says
   "Inbound from Facebook / Instagram ads (campaign)". Otherwise a new lead is created at stage New.
5. Consent is recorded only for what the person actually gave (see below).
6. A high-priority task "Call now: inbound lead from ..." is created for the caller, due now, with a notification.
   **Nothing is e-mailed or WhatsApped automatically.** A connector may be tied to an active flow (`enroll_flow_id`); the flow
   still obeys practice mode, caps, business hours, consent and the do-not-contact list.
7. One line goes in the capture log (last 500). It holds ids and a short reason, never names, e-mails, phones or tokens.

## Connector types

| Type | Address | How it proves itself |
| --- | --- | --- |
| Webhook (Zapier, Make, n8n, anything) | `POST /api/inbound/{id}` | token: header `X-Capture-Token`, `Authorization: Bearer`, or `?token=` |
| Website or landing-page form | `POST /api/inbound/{id}`, script at `GET /api/inbound/{id}/embed.js` | public form key `_form_key` (can only submit a form) or the token |
| Facebook and Instagram Lead Ads | `GET/POST /api/inbound/meta/{id}` | verify token on the handshake; `X-Hub-Signature-256` signed with the app secret |
| Google Ads lead form | `POST /api/inbound/google/{id}` | `google_key` in the body equal to the connector key |
| WhatsApp click-to-chat | none (uses the WhatsApp reply webhook) | not applicable |
| Google Sheet or CSV link | `POST /api/inbound/{id}/sync` (owner) and a 15-minute timer | not applicable |
| LinkedIn Lead Gen (via Zapier), IndiaMART, JustDial, TradeIndia, TikTok | the generic address with a preset field map | token |

All capture addresses are rate limited per address (120 a minute), refuse bodies over 256 KB, and answer 401 for a wrong token and 422 for a
lead with no usable e-mail or phone. A switched-off connector answers 403.

### Webhook (Zapier, Make, n8n)

    curl -X POST "https://YOUR-APP/api/inbound/cap1a2b3c4d" \
      -H "Content-Type: application/json" -H "X-Capture-Token: YOUR_TOKEN" \
      -d '{"name":"Asha Rao","email":"asha@example.com","phone":"9810012345","company":"Acme","utm_campaign":"diwali"}'

Answer: `{"ok": true, "status": "new", "lead_id": "...", "new": true, "merged": false}`; `status` is `new`, `merged`, `replay` or `refused`.

Recognised names (case and punctuation do not matter): name, full_name, first_name + last_name, email, phone, mobile, whatsapp, company,
organization, business name, title, designation, city, website, message. Anything else is kept as a custom field on the lead (up to 20).
JSON, form-encoded and multipart bodies work. **Field map**: if your tool uses other names, give the connector a map such as
`{"email": "contact.mail_address", "phone": "contact.numbers.0", "custom.budget": "answers.budget"}` (dotted paths; list items by index).
If the tool sends its own lead id, set it as `lead_id`, `leadgen_id`, `inquiry_id`... or an `Idempotency-Key` header.

### Website form

Create the connector, list the websites allowed to use it (leave empty to allow any), and paste the snippet:

    <div id="mcn-lead-form"></div>
    <script src="https://YOUR-APP/api/inbound/cap1a2b3c4d/embed.js" async></script>

The script holds only the public form key. It contains a hidden trap field (a bot that fills it is told "received" and nothing is stored) and
sends the time it was drawn; a form sent faster than the connector's minimum fill time (default 3 seconds) is treated the same way. The
response carries `Access-Control-Allow-Origin` only for an allowed website. It also sends UTM values, `gclid`/`fbclid`, the page address and
the referrer. To use your own HTML form, post form fields plus `_form_key`.

### Facebook and Instagram Lead Ads

1. Ads Manager: create a Lead Ads (Instant Form) and use it in an ad.
2. developers.facebook.com: your Meta App, add Webhooks, choose **Page**, Subscribe to this object.
3. Callback URL and Verify token: the two values shown on the connector. Subscribe to the `leadgen` field.
4. Subscribe the Page to the app.
5. Paste the **App secret** (App settings, Basic) and a **Page access token** with `leads_retrieval` into the connector.
6. Use Meta's Lead Ads Testing Tool, or **Send a test lead**.

Meta sends only ids; the app then reads the answers from `GET https://graph.facebook.com/{version}/{leadgen_id}` with the page token, through
the same guarded HTTP client as other outgoing calls (public addresses only, size cap, timeout). If Meta cannot be reached the webhook
answers 502 so Meta retries; replays are harmless. Payloads that already contain `field_data` (Zapier and Make Meta modules) are accepted
with the capture token and need no fetch.

### Google Ads lead form

Lead form asset, Lead delivery, Webhook: paste the webhook URL and the Key. `user_column_data` (FULL_NAME, EMAIL, PHONE_NUMBER, COMPANY_NAME,
JOB_TITLE, CITY ...), `campaign_id`, `adgroup_id`, `creative_id`, `form_id`, `gcl_id` and `lead_id` are read. Leads with `is_test: true` are
accepted but flagged as test leads: no task, no consent, not counted.

### WhatsApp click-to-chat

Turn the connector on. The first message from a number that matches no lead creates a lead (source WhatsApp message) with a "Call now" task, and
records WhatsApp consent because they wrote first. A STOP message or an erasure request never creates a lead. Matched numbers behave as before.

### Google Sheet or CSV

Publish the tab to the web as CSV (or paste an edit link; it is converted to the CSV export) and save the link. Columns are matched with the import
wizard's header mapper. Each row is remembered by a hash, so a sync only adds rows it has not seen. The link is fetched through the guarded client:
private and internal addresses are refused. Sheet rows get no "Call now" task by default and no consent unless you tick the box saying these people
agreed (a sheet of unknown origin is not proof).

### Presets

LinkedIn Lead Gen (through Zapier's LinkedIn Lead Gen Forms trigger), IndiaMART (Push API), JustDial, TradeIndia and TikTok Instant Forms
use the generic engine with a pre-filled field map and lead-id path. Their setup steps are on the connector.

## Attribution

A lead keeps `attribution` from its first capture (utm_source, utm_medium, utm_campaign, utm_content, utm_term, campaign_id/name, adset_id/name, ad_id/name,
form_id/name, platform, click_id (fbclid, gclid, ttclid), landing_url, referrer, captured_at). Only these keys are kept, as text, each cut to 300 characters.
`sources[]` lists every time the lead was seen, `{id, at, detail, attribution}`, and the Leads drawer shows it as "Where this lead came from".

## Consent (DPDP)

Conservative by design.

* A submitted form or ad lead form records consent for **e-mail and call** (ledger rows `given`, with the form or ad name as proof).
* **WhatsApp** only if the payload has an explicit yes (`whatsapp_optin`, `whatsapp_consent`, `wa_optin` ...) or the person messaged on WhatsApp first.
* A "no" is respected: `consent`, `agree`, `privacy`, `marketing_consent` = no records nothing at all; `email_optin`, `call_optin` or `whatsapp_optin` = no skips that channel.
* `lead.consent` (which unlocks WhatsApp sending and the Interested stage) is set only for WhatsApp consent. E-mail and call consent is in the ledger and in `inbound_consent`.
* A lead that had unsubscribed or withdrawn consent is merged but its stage and consent are untouched; a task says "Check before calling".

## Security

* Each connector has its own token (random, rotatable) compared in constant time. Tokens, the Meta app secret and page token live inside the connector's `tokens`
  container, which is encrypted at rest when `OUTREACH_SECRET_KEY` is set, left out of backups, masked in every read (full value shown once, on create or rotate) and never logged.
* Public capture addresses are the only `/api/inbound` paths without a login; everything else under `/api/inbound` and all of `/api/capture` is owner only.
* The capture log and the replay table hold ids only. Erasing a person (DPDP) is unaffected: their hash blocks later captures.
* Test leads are labelled "ZZ Test capture", tagged `test`, never merged into a real person, excluded from Dashboard and Insights, and removable
  (`DELETE /api/capture/connectors/{id}/test-leads`).

## Connector API (owner only)

`GET /api/capture/catalogue`, `GET/POST /api/capture/connectors`, `GET/PUT/DELETE /api/capture/connectors/{id}`,
`POST /api/capture/connectors/{id}/rotate-token` (`{"which": "verify"}` for Meta's verify token), `DELETE .../test-leads`,
`POST /api/inbound/{id}/test`, `POST /api/inbound/{id}/sync`, `GET /api/inbound/log`. `GET /api/lead-sources` lists the canonical sources for any user.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| 401 from the address | wrong or rotated token; for a form, the form key or the website is not allowed (403) |
| 422 | the lead has no valid e-mail or phone |
| 403 | the connector is switched off |
| `status: refused` | the person is on the do-not-contact list or was erased |
| `status: replay` | the same lead id, key or details were already captured |
| Meta handshake fails | the verify token on the connector differs from the one pasted in Meta |
| Meta leads do not appear | check the capture log: "Meta answered with error 190" means the page token expired; "no valid lead id" means a test ping |
| Public base URL shows localhost | set the public address under Setup so the connector shows the address platforms can reach |
| Sheet sync says private network | the link must be a public https address |
| No "Call now" task | the connector's call task is off (sheets default off), or the lead was a test lead |
