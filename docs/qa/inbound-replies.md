# QA report: reply capture, classifier, bounces

Branch `qa/inbound-replies` (worktree `qa-inbound-replies`). Scope: `app/replies.py`, `app/inbound.py`,
`app/privacy.py` (erasure wording), `app/channels/inbound_parse.py`, `app/channels/imap_poll.py`,
`/api/webhooks/{channel}/{provider}`.

Tests: `tests/qa/test_inbound-replies.py` (classifier + pipeline at volume) and
`tests/qa/test_inbound-replies_transport.py` (IMAP, webhooks, bounce pause). 488 tests in `tests/qa/`.
Full suite: **1236 passed** (the baseline was 748, before other QA branches were merged into main).

All fixture data is made up: `*.example` domains and `90000 1xxxx` numbers.

## 1. Corpus

| File | What it holds |
|---|---|
| `fixtures/inbound-replies/replies_corpus.json` | 208 replies: English, Hinglish, Hindi in Latin script, Indian-English phrasing ("do the needful", "kindly revert"), one-word replies, signatures, "Sent from my iPhone", Gmail/Outlook quotes (English, Hindi `प्रेषक:`, German `Von:`, French `a écrit`), forwards, disclaimers, OOO/leave/Freshdesk/Zendesk acknowledgements, DSNs (Gmail, Exchange 550 5.1.1, Zoho/Exim, Yahoo, Rediffmail/qmail), DPDP "delete my data", "wrong person, contact X", pricing questions, "call me tomorrow", sarcasm and negation. Reusable blocks (`{{gmail_quote}}` and so on) are expanded by the loader. |
| `fixtures/inbound-replies/replies_holdout.json` | 36 replies written **after** the fixes and never tuned against. They give an honest accuracy figure. |
| `fixtures/inbound-replies/eml/*.eml` | 11 raw RFC 822 messages: multipart/alternative quoted-printable UTF-8, base64 iso-8859-1 (Outlook, `References` only), RFC 2047 headers, Gmail DSN, Exchange NDR, Zoho/Postfix DSN with `text/rfc822-headers`, qmail inline copy, OOO with `Auto-Submitted`, Zendesk acknowledgement, newsletter, a stranger's "Re:", and a Hindi erasure request from an `other_contacts` address. |
| `fixtures/inbound-replies/webhooks.json` | Payloads for Resend inbound and events, SendGrid Event Webhook (a mixed batch), Postmark inbound, bounce (hard and soft) and spam complaint. |
| `fixtures/inbound-replies/leads.csv` | The leads: BOM, CRLF line endings, ALL CAPS names, "Pvt. Ltd." and "(P) Ltd", phones written as `+91`, `0`, dashes and spaces, padded email, a blank row. |

## 2. Classifier accuracy (rules plus the DPDP erasure override, no AI step)

| Label | Corpus before | Corpus after | Hold-out (after) |
|---|---|---|---|
| interested | 36/45 | 45/45 | 7/8 |
| not_interested | 23/30 | 30/30 | 6/6 |
| unsubscribe | 24/30 | 30/30 | 5/5 |
| not_now | 9/16 | 16/16 | 3/3 |
| wrong_person | 11/15 | 15/15 | 3/3 |
| question | 17/17 | 17/17 | 3/3 |
| auto_reply | 12/23 | 23/23 | 3/3 |
| bounce | 9/14 | 14/14 | 2/2 |
| other | 13/18 | 18/18 | 3/3 |
| **total** | **154/208 (74%)** | **208/208** | **35/36 (97%)** |

The fixes were tuned on the main corpus, so its 100% is a regression baseline, not an accuracy claim. The
hold-out set is the honest figure:

- With the original classifier: 31/36.
- After the corpus fixes: 33/36.
- After two more small fixes for its safety misses (a sarcastic false yes, and a missed "mail mat karna"): 35/36.

### Misclassifications found (original classifier)

**A no read as a yes.** This breaks PRD §4. Each of these replies would have recorded consent.

- ni-19 "Yeah right, as if we need another AI tool." → interested.
- ni-20 "Yeah, no." → interested.
- ni-21 "I don't think we need a sample…" → interested.
- nn-13 "Maybe later. Regards, … Sure Shot Placements" → interested. The signature decided the label.
- nn-16 "Not right now… Yes Staffing Pvt. Ltd." → interested. The company name decided the label.
- ar-05, ar-06, ar-15: vacation and holiday auto-replies containing "call me", "contact me" or "Sure" → interested.
- ot-13, ot-16 "Will check and revert" and "Got it, will discuss internally" with signatures → interested.
- Hold-out h-ni-06 "Sure, because what we really need is more vendor emails. No." → interested.
- Hold-out h-ot-03: "Yes Bank Tower" in a signature → interested.

**Read as an unsubscribe only because of our own quoted email or a legal disclaimer.** The lead was closed and
suppressed by mistake.

- int-32 and ni-30: Hindi Outlook header (`प्रेषक:`) not recognised as the start of a quote.
- int-33: German header (`Von:`) not recognised as the start of a quote.
- int-36, int-37, ot-18: the disclaimer "…delete all copies of this email" matched the erasure rule.
- ot-11, ot-12: the mail is only our quoted email. The fallback re-read the full text, including our "Unsubscribe" link.

**Missed stop or erasure requests.**

- un-21 "request erasure of my personal data".
- un-22 "Request for deletion of my data".
- un-23 "Never contact me again".
- un-24 "Don't WhatsApp me".
- un-25 "email na bheje".
- un-29 "…Remove it."
- Hold-out h-un-04 "Dobara mail mat karna".

**Missed auto-replies.** Freshdesk and Zendesk ticket acknowledgements, "automated response", "chutti par",
"limited access to email", "team will get back to you within", Gmail's "Delivery incomplete" (a temporary delay),
and hold-out h-ar-02.

**Missed bounces.** qmail ("wasn't able to deliver your message"), Yahoo ("unable to deliver your message"),
Exim ("could not be delivered to"), Gmail ("Message not delivered", "failed permanently").

**Other labels.**

- not_now: "not hiring currently", "hiring freeze", "next financial year", "festive season", "follow up in 2 weeks".
- wrong_person: "Main HR nahi dekhta, Ramesh ji se baat karo", "we don't do hiring", "Not my department",
  "I left the company".
- interested: "do the needful and send the details", "would like to try", "Please share.", "Pls call on …",
  "Can we connect on a call".

**Still wrong after the fixes:** hold-out h-int-07 "Ji, aap demo dikha dijiye." → other. This is the safe
direction: it creates a task for a person and never records consent.

### What changed in `app/replies.py` (same rule order, rules first)

- **Quote and signature cut lines.** `Von:`, `De:`, `Da:` and `प्रेषक:` headers; `--`; "Regards" and
  "Thanks & Regards" lines; `DISCLAIMER`, `CONFIDENTIALITY NOTICE` and "This e-mail and any attachments" lines.
- **Quote-only replies.** A reply that is only quoted text is now `other`. Before, it fell back to reading our own
  email.
- **Added wording, per label.**
  - bounce: qmail, Yahoo, Exim and Gmail phrasings.
  - auto_reply: vacation and holiday, ticket acknowledgements, automated replies, delivery delays.
  - unsubscribe: "never contact me", "don't WhatsApp", "remove it", "na bhej…", "mail mat".
  - not_interested: sarcasm markers, "we're good/sorted/all set" (but not "good to go"), a final bare "No.", "Na".
  - wrong_person, interested and not_now: the phrases listed above.
- **`app/privacy.py`.** The erasure rule also matches "deletion" and "erasure".

## 3. Pipeline checks (all pass)

**Every corpus reply through `apply_reply`.** 244 imported leads, each enrolled and each sent one reply:

- Consent is set only when the label is interested, and never for a reply whose expected label is not interested.
- Every non-auto reply stops the sequence. Auto-replies leave the stage and sequence alone.
- Unsubscribe moves the lead to unsubscribed, suppresses the address and writes a withdrawn row to the ledger.
- Bounce moves the lead to disqualified and suppresses the address.
- question, wrong_person and other each create exactly one task.
- An erasure request creates an `erasure_request` task.

**Closed leads stay closed.** Unsubscribed and customer leads replying "Yes" or "No thanks" get review or logged
only. No stage changes and no consent is recorded.

**IMAP morning mailbox** (11 raw messages through `POST /api/channels/inbound_email/imap/poll` with `FakeIMAP`):

- Matching worked by thread (`In-Reply-To`, or `References` only), by sender address, and by an `other_contacts`
  address in a different case.
- The newsletter was ignored and not stored. The stranger's "Re:" became one `unmatched_reply` task.
- Messages are marked `\Seen` after processing, and nothing is read twice.
- In read-only mode, a message redelivered under a new UID is handled once (dedupe by Message-ID).
- A processor crash stops the run at that message, and the message is retried on the next poll.

**Sequence over 8 days** (live sending with a fake transport, four runs per day): the lead who replied gets no
further email. The out-of-office lead and the silent leads get Email 2.

**Webhooks:**

| Provider | What was checked |
|---|---|
| Postmark inbound | Refused without the secret. Matched by thread. A retry is deduped. "Unsubscribe" from the quoted footer is not stored. |
| SendGrid Inbound Parse | multipart/form-data with a `charsets` map. The text part in iso-8859-1 is decoded correctly. |
| Resend inbound | Svix signature: rejected when bad, when stale (1 h old) or when only the token is given; accepted when valid. |
| SendGrid events (one mixed batch) | bounce suppresses; `blocked` is ignored; deferred, processed and open are ignored; spamreport unsubscribes; delivered sets `delivered_at`. A retried batch is not counted twice. |
| Postmark events | A HardBounce suppresses. A SoftBounce is ignored. A SpamComplaint unsubscribes. |

**Pause thresholds** (with `now=` time travel):

- Bounces: 6 of 300 sends (2.0%) does not pause; the 7th bounce does. 200 older sends outside the 7-day window are
  not counted.
- Complaints: 1 of 334 sends does not pause; 2 do. Eight days later the window has rolled on.
- Under 50 sends, nothing pauses.

## 4. Bugs fixed (each its own commit, each with a regression test)

1. **The classifier read a no as a yes, closed leads because of our own footer, and missed stop requests**
   (section 2). Severity: high. This is consent safety under PRD §4.
2. **Bounce reports without thread headers were ignored.** Severity: high for SMTP senders.
   - Exchange NDRs, Zoho/Postfix DSNs (`text/rfc822-headers`) and qmail/Rediffmail failure notices have no
     `In-Reply-To`. `process_inbound_message` dropped them as "not a reply to our outreach".
   - So with SMTP sending the dead address was never suppressed, and the sequence kept emailing it.
   - Fix: for `multipart/report` mail, and for qmail's "Below this line is a copy of the message", take the original
     Message-ID from the report (`email_from_mime`). A forwarded `message/rfc822` attachment is not affected.
3. **Resend temporary bounces suppressed the address permanently.** Severity: medium.
   - `email.bounced` carries `data.bounce.type`, which is `Permanent` or `Temporary`. Every bounce was treated as
     hard.
   - Now only Permanent bounces count. A payload without a `bounce` object is still treated as hard, as before.
4. **Webhook times kept their UTC offset.** Severity: low.
   - Postmark sends `BouncedAt: …+05:30`, and the time was stored with that offset.
   - The 7-day rate window compares ISO strings against UTC values, so events near the window edge were miscounted.
   - Aware times are now converted to UTC.

## 5. Product limitations and recommendations (not changed)

- **Resend inbound has no body.** Resend's `email.received` webhook sends metadata only ("Webhooks do not include
  the email body, headers, or attachments"). The app reads `data.text`, which is empty, so it classifies the subject
  line, usually as `other`. It has no `In-Reply-To`, so it matches by sender address only. The result is safe (a
  task, no consent) but every Resend reply needs a person.
  *Recommendation:* fetch the body and headers with the Received Emails API (`GET /emails/receiving/{email_id}`) and
  the saved API key, then classify that. The test documents today's behaviour.
- **Bounces found in the mailbox do not count toward the pause rate.** A DSN found by IMAP is applied through
  `apply_reply` (the lead is disqualified and suppressed), but it is not written to `delivery_events`. So
  SMTP-only senders never reach the bounce-rate pause.
  *Recommendation:* when the label is `bounce` and the provider is `imap`, also call `process_delivery_event`.
- **Auto-replies are recognised by body text only.** `Auto-Submitted` and `X-Autoreply` headers are not read.
  *Recommendation:* pass those headers through `email_from_mime` and treat `auto-replied`/`auto-generated` as an
  auto-reply before the rules run.
- **Rule order.** `auto_reply` runs before `unsubscribe`, so "I'm on leave, please remove me" is only logged.
  This is rare, and a person sees it in the timeline.
- **AI step.** Not exercised here (no AI transport). The existing rule still applies: an AI "interested" is never
  treated as consent.
- **One flaky test outside this area.** One full run hit a timing flake in
  `tests/test_security_web.py::test_public_routes_are_rate_limited_per_address[...meta_cloud...]` (403 instead of
  429). That file passes on its own and in later full runs.

## 6. Format sources

- Resend `email.received` (metadata only): https://resend.com/docs/webhooks/emails/received and
  https://resend.com/docs/dashboard/receiving/get-email-content
- Resend `email.bounced` (`bounce.type` Permanent / Temporary): https://resend.com/docs/webhooks/emails/bounced
- Resend / Svix signature headers: https://resend.com/docs/dashboard/webhooks/verify-webhooks-requests
- SendGrid Inbound Parse fields (headers, text, html, from, to, envelope, charsets, SPF, sender_ip):
  https://www.twilio.com/docs/sendgrid/for-developers/parsing-email/setting-up-the-inbound-parse-webhook
- SendGrid Event Webhook (bounce `type` bounce/blocked, deferred, spamreport, delivered, `sg_message_id`):
  https://www.twilio.com/docs/sendgrid/for-developers/tracking-events/event
- Postmark inbound JSON (FromFull, ToFull, MailboxHash, Headers[], TextBody, StrippedTextReply):
  https://postmarkapp.com/developer/webhooks/inbound-webhook
- Postmark bounce webhook (RecordType, Type HardBounce/SoftBounce, Inactive, BouncedAt):
  https://postmarkapp.com/developer/webhooks/bounce-webhook
- Postmark spam complaint webhook: https://postmarkapp.com/developer/webhooks/spam-complaint-webhook
- DSN structure (multipart/report, message/delivery-status, text/rfc822-headers): RFC 3462 / RFC 6522
  https://www.rfc-editor.org/rfc/rfc6522 and RFC 3464 https://www.rfc-editor.org/rfc/rfc3464
- Enhanced status codes (5.1.1, 4.2.2, 5.2.2): RFC 3463 https://www.rfc-editor.org/rfc/rfc3463
- The qmail bounce format ("Below this line is a copy of the message"): https://cr.yp.to/proto/qsbmf.txt
