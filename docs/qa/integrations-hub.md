# QA report: Integrations hub, custom connector, setup and go-live

Branch `qa/integrations-hub`. All provider traffic is faked (`tests/channel_fakes.py` FakeHTTP / SMTPFactory / FakeIMAP,
a fixed public DNS resolver). Fixture data is fictional (example / `.invalid`-style domains, 90000 1xxxx numbers).

## What is covered

| File | Scope |
|---|---|
| `tests/qa/test_integrations-hub.py` | Every catalogue card (40+): save with required fields, each required field enforced (blank / spaces refused), secrets masked in `/api/channels`, `/api/integrations`, `/api/readiness`, `/api/settings`; the wizard's re-save (empty secrets, masked header rows) and an API echo of masked values keep the real secrets; delete wipes keys from the data file; first-saved-is-active, switching active keeps every card's keys, `make_active`, refusals (unsaved card, OAuth calendar not connected, multi channels); SSRF on fetched URLs (metadata IP, RFC1918, loopback, ULA IPv6, plain http, userinfo, DNS rebinding); webhook listing, per-provider secrets, rotation (old secret refused, signature schemes not bypassed by token), public-address setting; caller / viewer roles get 403 on every integrations write and on the webhook list. |
| `tests/qa/test_integrations-hub_connect.py` | The Test button on every card in three worlds: provider success (documented reply shapes), auth failure (HTTP 401 / SMTP 535 / IMAP AUTHENTICATIONFAILED), network failure. Never 500, never leaks a secret, records `last_test`, card goes `error` and back to `configured`; passive cards (booking link, click-to-call, inbound/event webhooks) make no calls; test-before-save; two-day "wrong key, then right key" story; tests never flip live switches. |
| `tests/qa/test_integrations-hub_custom.py` | Custom HTTP: all 12 placeholders with quotes, backslash, newline, tab, Devanagari, emoji and `</script>` round-trip through JSON; URL placeholders percent-encoded (cannot add parameters or change host); form bodies; `body_type: none`; own Content-Type kept; broken templates refused on save (unknown placeholder, unquoted placeholder, trailing comma, placeholders in URL / headers, CRLF in headers); success_path / success_value matrix (case, booleans, numbers, list index, JSON array replies); id_path; SSRF via a host placeholder and DNS rebinding at send time; secret headers masked in the preview, reply headers (Set-Cookie) not echoed; 2 KB cut. Custom webhook: a 124-message SMS-gateway batch (mixed `+91` / `91` / `0` / numeric msisdn, blank text, shortcode), Exotel / Plivo / Twilio-WhatsApp / Mailgun form posts, a nested WhatsApp Cloud payload, Brevo inbound JSON, Mailgun event JSON with custom bounce values; secret via query, header or basic auth; the paste-a-sample preview. |
| `tests/qa/test_integrations-hub_golive.py` | Three-day go-live journey with a 150-row messy sheet; every live-switch blocker on its own (no / http / localhost / private https address, blank company, each footer contact alone is enough, no connected sender, deleted sender); `/api/channels/live` cannot flip email; WhatsApp / SMS switches stay practice without a provider; optional checklist rows follow hub cards; legacy `/api/integrations` mirrors the active hub provider for email, WhatsApp and calls, legacy saves show up in the hub, masked echo keeps secrets. |
| `tests/qa/test_integrations-hub_regressions.py` | One regression test per bug fixed below. |

## Bugs found and fixed (each its own commit)

1. **Custom connector reply preview was 2048 characters, not 2 KB** (`app/channels/custom_http.py`). A Hindi or emoji
   reply showed ~6 KB. Now cut on UTF-8 bytes at a character boundary. Severity low.
2. **Go-live checklist ticked an `http://` public address** (`app/routes_setup.py::_public_url_ok`). The checklist said
   "ready for a live test" while the live switch (`live_blockers`) refuses anything but https. Now https is required. Severity medium.
3. **Legacy `PUT /api/integrations/{id}` accepted any mode** (`app/main.py`). `{mode: "manual"}` on `sending_domain`
   (which only offers `api`) made the checklist's "sending domain" row done with no sender connected. Now refused with 400. Severity medium.
4. **Setup-page test buttons ignored hub providers** (`app/routes_setup.py`). With Interakt / Meta / Exotel / Twilio
   connected on the Integrations page, `POST /api/integrations/whatsapp/test` said WhatsApp "stays in practice mode, tell
   us your provider" and `/telephony/test` demanded a `{phone}` link. Now it says the channel is connected through that
   provider and points to the hub's Test button. Severity low.

## Product limitations and recommendations (not changed)

- **Checklist vs live switch: sender identity.** `ready_for_live_test` can be true while the switch still refuses for a
  missing company name / footer contact. Recommend a required "Sender identity" row (changes the PRD 5.9 list and
  `tests/test_phase4.py`, so left to the product owner).
- **Private-range check is partial.** Both `live_blockers` and the checklist treat `172.17.0.0`–`172.31.255.255`, `*.local`
  and `*.internal` hosts as public (`https://172.20.0.5` passes). Recommend using `ipaddress.ip_address(host).is_private`
  in both places.
- **Footer contact wording.** The blocker says "a postal address, or your website and a contact email", but any one of
  address / website / contact email is accepted. Either the message or the rule should change.
- **WhatsApp / SMS live switches have no prerequisites.** They can be switched on without a provider; harmless (sends stay
  `dry_run` without a connected provider) but the UI could grey the switch out until the channel is configured.
- **Custom webhook mapping reads one item per call unless the body is a JSON array.** A wrapper such as Brevo's
  `{"items": [...]}` can only be mapped as `items.0.…`, so a batch of several replies loses all but the first. Recommend an
  optional "items path" field.
- **Custom webhook email sender as an object.** `From: {"Name", "Address"}` objects need `From.Address`; only `email` /
  `Email` keys are recognised automatically.
- **Custom event mapping cannot tell temporary from permanent failures** (Mailgun `failed` with `severity: temporary`
  becomes a bounce). Recommend an optional "only when field X equals Y" condition, or a dedicated Mailgun events card.
- **Preview only masks headers.** A key typed literally into the URL or body template shows in the Send-test preview.
  The help text should say keys belong in a header ticked "secret".
- **Branding.** The custom connector's sample data and User-Agent still say "Outreach OS" (`SAMPLE`, `USER_AGENT`).
- Placeholder values that are falsy non-strings (e.g. `0`) render as empty strings; all current variables are strings.

## Formats researched (fixtures in `tests/qa/fixtures/integrations-hub/`)

| Fixture | Shaped after |
|---|---|
| `twilio_whatsapp_inbound.form` | Twilio messaging webhook request parameters: https://www.twilio.com/docs/messaging/guides/webhook-request |
| `exotel_sms_inbound.form` | Exotel incoming SMS callback parameters: https://developer.exotel.com/api/sms |
| `plivo_sms_inbound.form` | Plivo inbound message parameters: https://www.plivo.com/docs/messaging/use-cases/receive-sms |
| `mailgun_inbound.form` | Mailgun routes "forward" POST fields: https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/ |
| `mailgun_*` events in `webhook_payloads.json` | Mailgun event webhooks: https://documentation.mailgun.com/docs/mailgun/user-manual/events/webhooks/ |
| `dialog360_whatsapp_inbound` | WhatsApp Cloud API webhook payload (360dialog forwards it unchanged): https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples and https://docs.360dialog.com/docs/waba-messaging/webhook |
| `brevo_inbound` | Brevo inbound parsing webhook: https://developers.brevo.com/docs/inbound-parse-webhooks |
| `ses_sns_bounce` | Amazon SES notification contents via SNS: https://docs.aws.amazon.com/ses/latest/dg/notification-contents.html |
| `sms_gateway_batch.json` | A generic Indian SMS gateway batch (no single vendor); messy msisdn formats on purpose |
| `go_live_leads.csv` | An Excel / Apollo-style lead sheet: BOM, ALL CAPS, Pvt. Ltd., +91 / 0 / spaced phones |
| `provider_values.json` | Each card's required fields, with fake keys; shapes follow each card's own `docs_url` |

Success reply shapes in the connect tests follow each provider's send API docs (MSG91 flow `type: success`, Fast2SMS
`return: true`, Gupshup `status: submitted`, Zoom server-to-server OAuth `access_token`).

## Result

Full suite: 1202 passed, 6 skipped (the skips are OAuth calendar cards, which have no webhook or no required field without a default).
