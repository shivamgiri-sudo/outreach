# MCN Outreach (formerly Outreach OS) — Product Requirements Document

**Status:** working product, running locally in practice mode. Live sending is not switched on yet.
**Product it serves:** BookMyInterview (bookmyinterview.ai), an AI hiring platform in beta.
**Market:** Indian employers first, international later.
**Audience for this document:** anyone who wants to understand, run, rebuild or improve Outreach OS.

---

## 1. Problem

BookMyInterview screens job applicants once (a short voice answer plus a few multiple-choice questions). The best candidates then book their own interview slot. Any company that hires every day is a possible customer, in any industry and for any role.

The team needs a steady flow of these employers:
- Find them.
- Get their contact details lawfully.
- Start a human, honest conversation.
- Let them try the product free.
- Hand the interested ones to WhatsApp and a phone call.

Spreadsheets and ad-hoc emails don't scale. They don't remember who said what, and they make it easy to break consent rules.

## 2. Strategy the product supports

**"Try it free first" (the Jio approach).** Instead of selling first, let employers experience the product:
- The first role is free, with a cap (default: 30 days, 25 candidates screened, 5 interviews booked).
- Every lead gets a private sample-screen page drafted from their job title.
- Activation is tracked: drafted a role, published it, first candidate screened, first interview booked, converted.
- Growth comes from a referral loop, a free pilot on applicants they already have, and partner communities.

**Tone:** short, human, honest, never salesy. The product is in beta and has no customer case studies yet, so **no message may invent a customer, statistic or story**.

## 3. Users

| User | Needs |
|---|---|
| Outreach owner | Import lists, decide who to contact, watch the funnel, change rules and copy |
| Priya (caller) | A queue of leads who said yes, a script and honest objection answers, and outcome logging. Calls go through PD (progressive dialler) only, the same day, before 18:55 |
| Team member doing research | Add leads found by hand (team Google Sheet), enrich them, record where each detail came from |
| BookMyInterview product | Report activation events back (webhook) |

## 4. Principles (non-negotiable)

1. **Consent before WhatsApp.**
   - A recorded "yes", with its text and time, is needed before any WhatsApp message.
   - Free text is allowed only within 24 hours of the lead's last message.
2. **A "no" is never a "yes".**
   - Reply reading handles negation ("not interested", "I'm not sure"), stop words (English and Hinglish), auto-replies and bounces.
   - An AI guess of "yes" is never treated as consent; a person confirms it.
3. **Closed leads stay closed.**
   - Unsubscribed, disqualified and customer leads are never moved back by an automatic reply.
4. **Every message has an unsubscribe.**
   - The link is signed (`/u/<lead id>.<signature>`), so nobody can unsubscribe a person whose email they never saw. Unsigned links in emails sent before signing started keep working for 60 days after that email.
   - Opening the link asks for one click, so email link-scanners can't unsubscribe people.
   - Unsubscribes and bounces go to a suppression list that is always checked.
5. **Compliant sources only.**
   - Public business contacts only, robots.txt respected and the source of every field stored.
   - No automated LinkedIn scraping or messaging; LinkedIn drafts are sent by a person.
   - No anti-bot bypassing.
6. **Cold email from a separate domain**, never the main bookmyinterview.ai domain, with a warm-up ramp.
7. **India's DPDP Act** guides data handling:
   - Lawful purpose, an opt-out in every message, and an optional privacy notice link in every email footer.
   - On request, everything held on a person can be exported or erased. After erasure only a hash of their email and
     phone is kept, so they are never imported or contacted again.
   - Every consent given or withdrawn is written to an append-only consent ledger.
   - Cold leads past the retention period are listed for review; nothing is deleted automatically.
8. **Practice mode by default.**
   - Nothing leaves the computer until a provider is connected AND "Send for real" is switched on.
9. **Secure by default.**
   - Each person signs in with their own login and gets only what their role allows.
   - Saved keys are encrypted at rest when a server key is set.
   - The server never fetches private or internal network addresses.

## 5. Scope — features and requirements

### 5.1 Leads and list building
- **Import wizard** (Leads → Import leads): upload or paste any CSV, check the columns, see a dry run, then import with undo.
  - **Any column names.** Columns are matched to our fields by a large list of known names (case, spaces and punctuation don't
    matter), then keywords, then a fuzzy match, then by looking at the values (a column full of emails is an email column).
    Each column shows how sure the match is and can be changed by hand or set to "Don't import".
  - **Known layouts are recognised:** Apollo, LinkedIn Sales Navigator (Evaboot / Phantombuster style), Lusha, ZoomInfo, Hunter,
    Snov.io, job boards (Naukri, Indeed, Shine, Apna), IndiaMART / JustDial-style lists, Google Maps exports and the team sheet tabs.
    The column choices are remembered per layout (`settings.import_mappings`), so the next file of the same shape maps itself.
  - **Dry run first:** how many leads would be created, which existing leads get new details (and why they matched), duplicates
    inside the file, and rows left out with the reason (downloadable as a formula-safe CSV). Warnings cover shared inboxes
    (info@, hr@), personal addresses (Gmail, Yahoo), numbers outside India and addresses on the do-not-contact list.
    Options leave out shared inboxes or personal addresses.
  - **Commit is one atomic change**; **undo** removes the leads that import created if nobody has touched or contacted them since.
    Details it added to leads you already had stay.
  - **Quick paste** (the old simple form) is still there and now also understands Apollo-style column names.
  - Team Google Sheet tabs can still be imported on their own: **Leads**, **Demo Shared**, **Decision Makers**.
- **Standardisation** (applied to every imported row):
  - Names lose honorifics (Mr, Ms, Dr, Shri, Smt, CA) and ALL CAPS; first and last name columns are joined.
  - Company names lose legal suffixes for display (Pvt Ltd, Private Limited, LLP, Ltd, Inc, OPC); the original is kept in `company_legal`.
  - Common Indian HR titles are tidied (HR Head, TA Lead, HRBP, Founder & CEO), with a seniority (owner, c-level, vp, head,
    manager, executive) and a department (Founder, HR, TA, Ops, Other).
  - Emails are lower-cased and checked; phones become +91 numbers (mobiles and landlines with STD code) or international numbers;
    several numbers in one cell are split.
  - Websites become the company domain (eTLD+1, so `careers.acme.co.in` is `acme.co.in`); free-mail and social sites are not company sites.
  - City aliases (Bangalore/Bengaluru, Gurgaon/Gurugram, Bombay/Mumbai, NCR), state from city, country codes (India → IN),
    and company size buckets (1-10 … 1000+) with a number estimate.
- **Duplicates and merging** (within the file and against saved leads), strongest match first: same email; same Gmail inbox
  (dots and +tags ignored, for matching only); same phone; same company domain and person; same company domain; similar
  company name (token similarity ≥ 0.92) in the same city.
  - A lead is a company: another person at the same company is kept in `other_contacts`.
  - Merging only fills empty fields. A different value for a filled field is kept for review in `merge_conflicts`;
    `field_sources` records where each field came from, and the timeline says what was merged.
  - Rows whose email is on the do-not-contact list are skipped. An import never changes a lead's stage, so unsubscribed and
    disqualified leads stay that way.
- **Data hygiene page:** gaps (no email, phone, decision maker, website), invalid / shared / personal emails, numbers outside India,
  stale leads, differing values to keep or replace, and duplicate groups shown side by side with the reason they match.
  - "Keep this one, merge the others in" keeps every person, phone and timeline event, keeps the strictest status (unsubscribed,
    disqualified, consent withdrawn) or else the most advanced stage, records `merged_ids`, and moves outbox, inbox, task and
    WhatsApp rows to the kept lead.
  - "Standardise existing leads" applies the same clean-up to older leads after a preview. Emails, stages and consent are not changed.
- **Hiring signals from job pages:**
  - Read a careers page URL, pasted HTML (JSON-LD JobPosting) or a CSV.
  - Open roles, job age and reposts raise the score.
- **Decision Makers import:** people found by hand are attached to the matching company (ignoring "Pvt Ltd" and text in brackets).
  - The first person fills empty fields; others are kept as additional contacts.
  - Nothing is overwritten, and running it again is safe.
- **Leads screen:**
  - Search, filter by stage or temperature, sort by company, stage or score.
  - Select many leads to start the sequence or download a CSV.
  - Shown as cards on phones.

### 5.2 Enrichment and data sources
- **Built-in website crawler:** reads public contact pages (home, contact, about) politely and respects robots.txt.
- **Provider comparison ("Data sources"):** run several providers on the **same** sample of leads (Indian leads first) and compare them before committing to one.
  - **Measures:** coverage, emails and phones found, average confidence, credits used, cost per contact, and bounces once sending starts.
  - **Saving:** nothing is saved to leads until someone clicks "Use these results", which fills only empty fields and tags the source.
  - **Providers today:** own crawler, Hunter, Firecrawl (cloud or self-hosted), and Apollo (needs a contact name).
  - **Scraper import:** the JSON output of the open-source *website-email-contact-scraper* can be imported. Run it locally without bot-wall bypass or proxies.
- **Adding a provider:** add an entry to `app/providers.py` and `app/integrations.py`.

### 5.3 Scoring and suggestions
- The score runs from 0 to 100 and is built from five buying signals: hiring volume (30), hiring pain (25), fit (15), engagement (20) and intent (10).
- Bands: cold below 40, warm 40–69, hot 70 and above.
- Each lead shows a suggested next step, a channel, and what to say.

### 5.4 Email sequence
- **Steps:** four, on day 0, 3, 7 and 14.
- **Email 1:** short, with no link, ending "Want me to send it?".
- **Later steps:** a useful tip, a "right person?" check, and a permission close that offers a sample ("reply 'sample'").
- **Variables are cleaned:**
  - Company names lose legal suffixes and ALL CAPS.
  - First names lose honorifics.
  - Roles lose codes.
  - When a variable is missing, a no-role version of the email is used.
- An A/B setting can put the demo link in Email 1.
- **Sending rules:**
  - One send run at a time.
  - Each send is claimed before it goes out, so a crash never repeats a send.
  - Leads are never re-enrolled, so no one gets Email 1 twice.
  - Cold steps stop when a lead becomes warm (interested, WhatsApp active, call done, trial).
  - At least 2 days between steps, even after downtime.
  - A few sends per run (default 5), so the daily volume is spread out.
  - One email per company domain per day (free-mail addresses are not grouped).
  - Warm-up for live sending: 10 a day in week 1, 20 in week 2, 35 in week 3, then the configured cap.
  - Mon–Fri, 09:00–18:00 IST only.
  - Failed sends retry after 1 hour, at most 3 attempts.
  - Nothing goes out with an unfilled placeholder.
- **Provider:** Resend, with List-Unsubscribe and one-click headers.

### 5.5 Replies
- A pasted reply is classified as one of: interested, not_interested, unsubscribe, not_now, wrong_person, question, auto_reply, bounce or other.
- **What each reply does:**
  - **Interested:** records consent and moves the lead to Interested. This only happens while the lead is still being prospected; otherwise a review task is created.
  - **Not interested:** moves the lead to Disqualified.
  - **Unsubscribe:** unsubscribes the lead and suppresses the address.
  - **Bounce:** suppresses the address and stops the sequence.
  - **Auto-reply:** logged only.
  - **Question, wrong person, other:** creates a task for a person.
- **AI reading (optional):** used only when the rules return "other". An AI "interested" becomes a review task, never consent.
- **AI reply drafts (optional):**
  - Lead text is wrapped in tags and treated as untrusted data.
  - The draft mirrors the lead's language (English, Hindi or Hinglish) and is at most 70 words.
  - Pricing, legal or unknown topics are escalated to a person.
  - Without AI, template drafts are used.

### 5.6 Free experience (activation)
- **Demo page:** a private page per lead showing a sample screen.
  - Questions vary by role family (sales, support, operations, tech, other).
  - It is labelled as a sample and as beta, and has a claim link (with referral code and UTM) and a stop link.
- **Activation funnel and nudges:**
  - The funnel runs invited, demo opened, role drafted, role published, first candidate, first booking, converted.
  - Nudges fire when a lead stalls.
  - An honest heads-up is shown as the free limit approaches.
- **Other free-experience features:** free pilot requests, referrals (both sides get bonus screens), a partners list, and a capacity calculator.
- **Webhook:** the BookMyInterview product reports activity to `POST /api/activation/event` with an `X-Webhook-Secret` header, compared in constant time.

### 5.7 WhatsApp, SMS, calls and meetings
- **WhatsApp templates:** first message after yes, demo-not-opened nudge, draft-not-published nudge, and pilot offer. Each has a Hinglish version and "Reply STOP to opt out".
- **WhatsApp providers:** Meta Cloud API, Gupshup, Interakt, WATI, AiSensy or any provider through the custom connector.
  - `send_whatsapp()` (app/channels/whatsapp_send.py) returns `dry_run` unless a provider is connected **and** "Send for real" is on for WhatsApp.
  - The app's message names map to the provider's approved template names ("Template names" field).
  - The lead panel still queues messages in practice mode until app/whatsapp.py calls `send_whatsapp()` (core branch).
- **SMS (India):** MSG91 (flow API), Twilio, Fast2SMS or custom. The setup steps explain DLT (entity ID, header, template ID) and the promotional window (10:00–21:00, no DND numbers). `send_sms()` is `dry_run` unless "Send for real" is on for SMS.
- **Replies on WhatsApp and SMS:** provider webhooks are normalised and handed to `process_inbound_message` (app/inbound.py, core branch), which handles STOP.
- **Meetings:**
  - "Book a meeting" in the lead panel uses the active calendar: Google Calendar (Meet link), Microsoft 365 (Teams link), Zoom, or a custom API.
  - Booking-page providers (Cal.com, Calendly, any booking link) give a link prefilled with the lead's name and email instead.
  - Cal.com and Calendly booking webhooks attach the booking to the lead whose email (or other contact's email) matches, with a "Meeting booked" timeline event. Consent is never changed by a booking.
  - Times are entered and shown in IST, with a heads-up outside Mon–Sat 09:00–18:00. A Meetings page lists upcoming meetings with join links.
  - **Calendar invites:** Google and Microsoft email the lead themselves. Zoom, a custom calendar API and a plain booking link used with a fixed time don't, so the app emails an `.ics` invite (UTC times, organizer = your sending mailbox, the join link). "Send our own invite" adds one for Google or Microsoft too. Cancelling sends the matching cancellation. With "Send for real" off the invite is kept as a practice row.
  - Reminders (24 h and 1 h to the lead, 15 min to the owner) carry the join link and never fire for cancelled or done meetings.
  - **No-show check:** 30 minutes after a meeting ends without being marked done, the owner gets one task: "Mark the meeting done or follow up".
- **Call desk:**
  - Shows leads who said yes, with a click-to-call link built from your dialler's URL template (`{phone}`).
  - The script has an English and a Hinglish opener and an honest "proof" line (beta, no case studies yet).
  - There are 10 objection answers, and outcomes are logged.
  - Bridged calls (agent phone first, then the lead) can go through Exotel, Knowlarity, Twilio Voice or a custom API: `start_call()` in app/channels/calls.py, `dry_run` while no calls provider is connected.
- **LinkedIn:** a connection note (300 characters or fewer), a first message and a post comment, copied and sent by a person.

### 5.8 Today, workflows and insights
- **Today:** one ranked list:
  1. sending problems,
  2. replies to handle,
  3. calls to make,
  4. free-trial nudges,
  5. hot leads not yet contacted.

  Each item opens the lead, and tasks can be marked done.
- **Simple rules** (the page was called Workflows): "when X happens, do Y". For several steps, waits and branches, see 5.11.
  - Triggers: reply label, stage change, activation event, no reply for N days.
  - Actions: WhatsApp template, task, stage change, note.
  - Each rule runs once per lead.
- **Insights:** totals, reply and positive rates, results by source and by sector, and top leads.
- **Prompt generator:** builds system prompts for email, WhatsApp or voice.
  - Guardrails always come last and override anything the user adds.
  - No invented facts.

### 5.9 Setup and go-live
- **Setup page tabs:**
  - **Go-live checklist** (required: leads, public address, sending domain, test email, free limits confirmed; last switch: real sending).
  - **Connections:** each connector has setup steps, masked keys and a "Test connection" button. It links to the Integrations page.
- **Integrations page (the hub):**
  - One tab per channel: Email sending, Reply capture, Bounces, WhatsApp, SMS, Calls, Calendar & meetings, Lead data, AI, Custom, and a list of webhook addresses.
  - Each provider card shows a status (Not set up, Missing details, Connected, Error), a "Recommended" badge where sensible and the last test result.
  - Clicking a card opens a 4-step wizard:
    1. What you need: plain numbered steps and a link to the provider's dashboard.
    2. Your details: fields with help text and show / hide for secrets.
    3. Webhook address or Connect: addresses and secrets with copy buttons, or the OAuth redirect URI and a Connect button.
    4. Test, then "Make this the active provider".
  - Cost notes never state prices; they say "check the provider's site".
  - **Custom connector** for any provider: method, https URL, headers (secret values masked), a body template with click-to-insert `{{placeholders}}` (JSON-escaped in JSON bodies), a success field and a message-ID field. "Send test" shows the request (keys hidden) and the reply (first 2 KB).
  - **Custom webhook** for any provider: a private address plus a field mapping, with a "paste a sample payload" preview of what the app will read.
  - **Email providers:** Resend, SendGrid, Postmark (replies only; Postmark forbids cold email), Amazon SES (SMTP), any SMTP server, Gmail / Google Workspace (app password), custom. Port 25 is never suggested (most clouds, Oracle included, block it).
  - **Reply capture:** IMAP mailbox check (every 5 minutes with `OUTREACH_AUTORUN=1`, or "Check now"), Resend / SendGrid Inbound Parse / Postmark inbound webhooks, custom webhook.
  - **Bounces and complaints:** Resend, SendGrid and Postmark event webhooks, normalised and handed to `process_delivery_event` (app/inbound.py).
  - Webhooks are open without login only when their per-provider secret (or the provider's signature) checks out. OAuth callbacks are open only with a valid signed `state`.
- **Login and roles:**
  - The admin password (`OUTREACH_ADMIN_PASSWORD`) signs in as owner. Owners can add team logins (email and password, at least 10 characters) on the Setup page.
  - Roles: **owner** (everything), **caller** (leads, calls, meetings, tasks; no settings, integrations, campaign rules, live switches, sending runs or CSV exports), **viewer** (read only).
  - Tokens are signed with a random per-install secret (never shown by any API), last 12 hours and renew themselves after 6. Signing out revokes the session. Changing a password signs out that person's old sessions.
  - **Without a password the API only answers on this computer**, and requests through a proxy or tunnel are refused.
- **Abuse limits and headers:**
  - Per-address rate limits: login 5 a minute (plus a lockout after 5 wrong passwords), unsubscribe pages 30, demo pages 60, webhooks and activation events 120.
  - Behind the local proxy (Caddy on 127.0.0.1) the real address comes from the last `X-Forwarded-For` hop; nobody else can set it.
  - Every response gets `nosniff`, a referrer policy, no framing and a Content-Security-Policy; HSTS when the request came in over HTTPS. The demo page's one script runs with a per-request nonce.
  - The API docs (`/docs`, `/openapi.json`) are off unless `OUTREACH_ENABLE_DOCS=1`.
- **Saved keys:** with `OUTREACH_SECRET_KEY` set, API keys, passwords, OAuth tokens, webhook secrets and the server secret are encrypted in `store.json` (older plain values and backups are converted at start-up). Without it Setup shows "Set OUTREACH_SECRET_KEY to encrypt saved keys".
- **Fetching addresses:** the website crawler, Firecrawl, the custom connector and provider calls all go through one SSRF guard: public addresses only (metadata and private ranges refused), the checked IP is the one connected to, redirects are re-checked hop by hop (API calls never follow them), and errors only say "Couldn't fetch that page." A self-hosted service on a private address (e.g. Firecrawl on `localhost:3002`) needs `OUTREACH_ALLOW_PRIVATE_HOSTS` on the server.
- **DPDP (owner only):** `GET /api/leads/{id}/export`, `POST /api/leads/{id}/erase` (`{"confirm": true}`), `GET /api/compliance/retention`, `GET /api/compliance/consent-ledger`. A reply asking to delete their data withdraws consent and creates an "Erasure request" task. Both actions are also on the lead panel.
- **Quick search:** Ctrl+K jumps to any page or lead. Page addresses survive refresh and the back button.

### 5.10 Campaigns (one click: find, enrich, check, start)
- **A campaign** is saved targeting + sources + rules (`campaigns` collection): industries, cities, roles they hire for,
  company size, keywords; sources; enrichment choices; rules (lowest score, default 40; business email only; skip shared
  mailboxes; most new leads per run, default 50; start the sequence automatically, off by default; who gets the update).
- **Sources (compliant only):**
  - Company careers pages (link, or pasted page HTML when a site blocks bots; only its JSON-LD is kept): schema.org JobPosting through the same
    parser and job store as "Read job pages". Job-board links (Naukri, LinkedIn, Indeed…) are refused, never fetched.
  - Apollo People API Search (`mixed_people/api_search`): HR heads, talent leads and founders at matching companies,
    capped by "most results per paid search".
  - Apollo hiring companies: Organization Search with the job-title filter, then each company's job postings
    (open roles, matching titles, oldest posting).
  - Google Places Text Search (New), with the owner's key (Integrations → Lead data → Google Places): "<industry> in
    <city>" → name, website, office phone (marked `phone_type: business`). Social pages are never used as the company site.
  - Leads already imported, picked by id (early stage, not yet emailed).
- **A run** (`POST /api/campaigns/{id}/run`): discover → merge rows for the same company (extra people become other
  contacts) → dedupe against existing leads and the suppression list → add new leads tagged `campaign_id`, with a
  "Added by campaign … from …" timeline event and `provenance` per field → enrich (website crawler, Hunter, Firecrawl,
  Apollo match by person id; empty fields only) → verify (format, business domain, shared mailbox; ZeroBounce or
  NeverBounce when connected, else "unverified") → score → qualify → start the sequence for qualifying leads only when
  allowed, through the same `enroll_lead` as the Enrol button.
  - Never re-added: suppressed addresses, and any company with an unsubscribed or disqualified lead. Leads past the
    early stages are never touched; an early-stage company lead without an email gets only its empty fields filled.
  - The campaign never sends. The sequence engine sends with all of 5.4's rules (practice mode, caps, warm-up,
    business hours, one email per domain per day, suppression).
  - A **preview** (`dry_run`) searches and scores but saves nothing and does not look up missing emails.
  - Each run keeps a report: found, new, already known, skipped, emails / phones found, verified, qualified, started,
    errors per source in plain words, and estimated credits. Running again the same day adds no duplicates.
- **Updates for the caller:** after every run, "Campaign <name>: N new leads, M enrolled" (in-app, and email when email
  reminders are on). A yes-reply call task names the campaign. A **morning summary** goes to the caller at or after
  09:30 IST on weekdays, once a day: calls due today, overdue tasks, new yes replies, meetings today, campaign runs.
- **Daily runs:** "Run every weekday automatically" sets the campaign to running; the background timer
  (`OUTREACH_AUTORUN=1`) runs it at most once per IST day, Mon–Fri 09:00–18:00 IST (a manual run counts).
- **API:** `GET/POST /api/campaigns`, `GET/PUT/DELETE /api/campaigns/{id}` (PUT changes only what it is given),
  `POST /api/campaigns/{id}/run` `{dry_run}`, `POST …/pause`, `POST …/resume`, `GET …/runs`, `GET …/leads`,
  `GET /api/campaign-sources`.

### 5.11 Flow builder (Outreach → Flow builder)
- **What it is:** a mind-map style editor (like N8N) where a non-technical owner draws a communication flow:
  a trigger, then checks, waits and actions across email, WhatsApp, SMS, LinkedIn tasks, calls, meetings, stages and webhooks.
  Data: `flows` (nodes + edges, status draft / active / paused, version) and `flow_runs` (one run per lead per flow).
- **Steps:**
  - Triggers: added by hand (lead panel or the flow's Enrol box), new lead added (filters: source, campaign, tag, city,
    country; only leads added after activation), reply (interested, not interested, question, not now, wrong person, any),
    stage changed, meeting booked, product activity, no reply for N days after an email (cold stages only).
  - Logic: If / else on lead fields (score, band, stage, city, country, title, persona, open roles, source, campaign, tag,
    has email / phone / consent / meeting / replied; is, is not, more / less than, contains, is one of, is filled in);
    A/B split (sticky per lead, recorded on the run); Wait (days / hours, optionally until Mon–Fri 09:00–18:00 IST);
    Wait for a reply (up to N days, a path per reply label, "any reply" and "no reply").
  - Actions: email, WhatsApp template, SMS, LinkedIn task (start the LinkedIn sequence, or one task with a message),
    call task (next call slot), meeting link (booking link + task with a booking-link calendar, else a task), set stage,
    add tag, notify a person, webhook (POST JSON), stop. Actions that can be refused have an "if refused" path; without
    one the run stops with a timeline note.
- **Rules nobody can draw around:**
  - Email goes through the sequence engine's composer, sender and outbox: practice mode until "Send for real", daily cap
    and warm-up shared with the sequence, a few sends per tick, one email per company domain per day, Mon–Fri 9–18 IST,
    provider pause, postal-address rule, no unfilled placeholder, signed unsubscribe link, idempotency key and Message-ID,
    and the sequence's send lock. Every flow email must contain `{{unsub_link}}`.
  - WhatsApp uses `whatsapp.send` (recorded consent, templates only, 24-hour rule). SMS needs recorded consent and is
    practice until SMS is connected and switched on. Stage changes obey `can_move`; closed leads stay closed.
    Webhooks need a public https address (SSRF guard, no redirects) and send contact details only when ticked.
  - Unsubscribed, disqualified, bounced or suppressed leads are never started and running ones stop before the next step.
    A reply stops the run unless the flow is waiting for it ("Stop on reply", on by default); cold flows also stop for
    warm leads. A lead is never enrolled twice in the same flow.
  - Each step is claimed on the run before it acts and recorded after: two ticks never repeat it, and a crash leaves the
    run marked for a person to check. Loops must contain a real wait (checked on save); runs stop after 200 steps.
  - Editing an active flow's steps makes a new version; leads already in it finish on their version. An active flow
    can only be saved complete; pause it to keep a draft. Only owners can change flows; callers can read them and stop runs.
- **Engine:** the autorun minute loop (`OUTREACH_AUTORUN=1`) picks up polled triggers and advances due runs, writing
  nothing when nothing is due. Replies, stage changes and product events start flows through the same hook as Simple rules.
- **Editor:** SVG canvas (pan, zoom, grid snap, drag to connect, select a connection to delete it), palette (click or drag),
  plain-English settings with placeholder chips, problems shown on the step in red, undo / redo (Ctrl+Z / Ctrl+Y),
  auto-layout, Save, Validate, Simulate on a lead (no writes; path highlighted and listed), Activate / Pause, Enrol and
  "Leads in this flow". Keyboard: Tab to a step, arrows move it, Enter edits, Delete removes. A List view shows the flow
  as nested steps (default on phones). The lead panel's Flows section lists the lead's runs with Stop and Start.
- **Templates:** Cold email 4-step (the sequence's four emails on day 0, 3, 7, 14), Yes → WhatsApp + Priya call,
  LinkedIn + email combo. `POST /api/flows/templates/{key}` makes a draft copy.
- **API:** `GET/POST /api/flows`, `GET/PUT/DELETE /api/flows/{id}`, `GET /api/flows/catalog`, `GET /api/flows/templates`,
  `POST /api/flows/templates/{key}`, `POST /api/flows/{id}/validate|activate|pause|enroll|simulate`,
  `GET /api/flows/{id}/runs`, `GET /api/leads/{id}/flows`, `POST /api/flow-runs/{id}/stop`, `POST /api/flows/tick`
  (`now` honoured only with `OUTREACH_TEST_MODE=1`).

### 5.12 LinkedIn assistant (assisted, not automated)
- **The rule:** LinkedIn forbids automation, so a human clicks every action on LinkedIn. The Chrome extension
  (`extensions/linkedin-assistant/`, Manifest V3, plain JS) is an assistant. It must not click LinkedIn buttons, press
  Send or Connect, submit forms, scroll or page through search results or lists, crawl or scrape profiles, simulate typing
  or mouse events, use human-like or random delays, rotate anything or hide itself. `tests/test_extension_rules.py`
  reads the extension source and fails on `.click(`, mouse / keyboard `dispatchEvent`, timers and random jitter,
  and scroll calls, and checks the manifest stays narrow.
- **What it does:** reads the one profile page the person opened (name, headline, company, profile link) to match a
  lead; shows a side panel with the message from the LinkedIn sequence and a Copy button; fills the connection note or
  message box once, only when the person presses its own "Insert message" button (never over text already typed without
  asking); notices read-only that the box closed or the person shows as 1st degree, then asks the person to confirm
  "I sent it", "They accepted" or "They replied" with one click. Nothing is reported silently.
- **Pairing:** `POST /api/extension/pair` (owner or caller; viewers refused) returns a long random token once; only its
  SHA-256 is stored (`settings.extension_tokens`, never returned by any API). Tokens expire after 90 days, at most 10 per
  person, listed without values (`GET /api/extension/tokens`) and revoked (`DELETE /api/extension/tokens/{id}`; owners
  revoke any, others their own). Web app: LinkedIn outreach → Chrome assistant.
- **Narrow principal:** a token authenticates as its creator (role caller; viewer if that person became a viewer; gone
  if the person was removed) and may call only the routes below. Everything else, including `/api/leads`, settings, users
  and token management, answers 403.
- **Routes:**
  - `GET /api/extension/match?profile_url=&name=&company=`: lead by LinkedIn `/in/<slug>` (query, trailing slash, regional
    domains and case ignored), else by exact name + company when that is unique. Returns `lead_id, name, company, title,
    linkedin_seq {status, next_step, due}, message {step, kind, text}, can_send, reason`. Unsubscribed or disqualified
    leads, stopped or finished sequences, steps waiting for acceptance, steps not yet due and "already sent today" never
    return a message.
  - `POST /api/extension/leads/{id}/sent {step}` (only the step currently offered), `/accepted` (only while waiting for
    acceptance), `/replied {text}`: they call the same LinkedIn sequence functions as the web app and only record what
    the person confirmed.
  - `GET /api/extension/queue`: today's due steps with a verified http(s) profile link.

## 6. Out of scope (on purpose)
- Automated LinkedIn scraping, auto-connecting or auto-messaging, and applying to job posts to pitch employers.
- Bypassing anti-bot protection or rotating proxies to avoid blocks.
- Sending from the main bookmyinterview.ai domain.
- Inventing testimonials, numbers or case studies.

## 7. Architecture

```
web/  React 18 + Vite, plain CSS (styles.css), Playwright browser tests (web/e2e)
app/  FastAPI
  main.py                 app factory, login middleware, lead/stage/consent/notes routes, router wiring
  auth.py routes_auth.py  tokens (server secret, renewal, revocation), users and roles, the role guard
  web_security.py         real client address, rate limits, security headers
  unsubscribe.py          signed unsubscribe links and the 60-day grace for older ones
  privacy.py routes_compliance.py   hashed suppression, consent ledger, export / erase / retention
  secretbox.py            encryption of saved keys in store.json
  store.py                JSON-file store: atomic saves (temp file + rename), locked update(), patch_leads()
  lead_model.py hygiene.py scoring.py stages.py suggest.py
  sequences.py            copy, variable cleaning, variants, due-step logic, business hours
  sequence_runner.py      the sending engine (rules in 5.4)
  senders.py              Resend sender + test email
  replies.py ai.py        rule classifier, AI drafting/reading with untrusted-data guards
  whatsapp.py workflows.py activation.py capacity.py demo.py growth_core.py
  jobs.py scraper.py enrich.py providers.py sheet_import.py apollo.py
  campaigns.py campaign_pipeline.py campaign_sources.py   campaigns (5.10): run, pipeline steps, discovery adapters
  digest.py               the caller's 09:30 IST weekday summary
  routes_*.py             outreach, growth, jobs, enrich, providers, whatsapp, workflows, insights, setup, today, channels, campaigns
  inbound.py              STUB in this branch: process_inbound_message / process_delivery_event (core branch implements)
  channels/               Integrations hub
    registry.py catalog_*.py   channels and providers: fields, steps, docs links, webhook needs
    config.py                  saved provider settings, masking, active provider, test results, tokens
    email_send.py whatsapp_send.py sms_send.py calls.py calendar.py oauth.py   provider adapters
    custom_http.py netguard.py http.py   custom connector, the one SSRF guard (pinned IPs), no-redirect HTTP transport
    invites.py                 .ics meeting invites and cancellations
    inbound_parse.py imap_poll.py webhooks.py meetings.py   inbound normalisation, IMAP, verification, meetings
    routes_hooks.py routes_meetings.py testers.py poller.py
tests/                    pytest (fake transports for every external API; fake SMTP and IMAP clients)
  header_map.py standardise.py places.py lead_import.py lead_merge.py hygiene_report.py   smart import, merging, hygiene (5.1)
  routes_*.py             outreach, growth, jobs, enrich, providers, whatsapp, workflows, insights, setup, today, import
tests/                    pytest (fake transports for every external API)
docs/PRD.md               this document
```

- **External calls go through injected transports** (`email_transport`, `ai_transport`, `apollo_transport`, `fetcher`, provider `get`/`post`, `campaign_transports`). Tests never touch the network.
- **Data:** one JSON file (`data/store.json`, git-ignored). Collections: leads, integrations, stories, templates, tasks, outbox, settings, suppressed, jobs, partners, whatsapp_outbox, workflows, provider_runs, campaigns.
- **External calls go through injected transports** (`email_transport`, `ai_transport`, `apollo_transport`, `fetcher`, provider `get`/`post`). Tests never touch the network.
- **Data:** one JSON file (`data/store.json`, git-ignored). Collections: leads, integrations, stories, templates, tasks, outbox, settings, suppressed, suppressed_hashes, consent_ledger, revoked_tokens, jobs, partners, whatsapp_outbox, transactional_outbox, workflows, provider_runs.
- **Hub settings** live in `integrations`, one entry per channel (`sending_domain`, `inbound_email`, `email_events`, `whatsapp`, `sms`, `telephony`, `calendar`):
  `{mode, values: {provider, ...active provider's fields}, providers: {<id>: {values, last_test{ok,message,at}, tokens, state}}, webhook_tokens: {<id>: secret}}`.
  - `values` mirrors the active provider, so older code keeps reading it.
  - OAuth tokens and webhook secrets are never returned by any API. Provider secrets come back as ••••last4, and saving a masked value keeps the real one.
- **Settings added:** `server_secret` (signs login tokens, unsubscribe links and OAuth state; never returned), `users` (team logins, scrypt hashes; never returned), `live_whatsapp`, `live_sms`, `privacy_url`, `retention_days_cold`, `unsub_signed_since`.

### Lead record (main fields)
`id, company, domain, website, contact_name, title, email, email_is_role, phone, phones, role, city, country, company_size, open_roles, source, stage, consent{text,at,source}, sequence{enrolled_at, sent_days, last_sent_at, stopped, in_flight, blocked, failed_attempts}, engagement{replies,…}, activation{step, usage,…}, demo_token, referral_code, linkedin_url, other_contacts[], technologies[], company_socials{}, meetings[{id, provider, title, start, end, join_url, status, created_at, external_id, attendee_email}], timeline[{at, kind, text}]`
Campaign leads also carry `campaign_id, provenance{field: "source:url"}, email_check{status, provider}, campaign_check{score, qualified, reasons}`, and where found `company_phone, phone_type, apollo_person_id, google_place_id`.

## 8. Success metrics
- Positive-reply rate per 100 sends. Judge by replies, not opens; tracking pixels are not used.
- Bounce rate under 2%, and complaints close to zero.
- Leads reaching "role drafted" and "first candidate" in the free experience.
- Conversion from free to paid.
- Data-source cost per usable contact.

## 9. Known gaps and roadmap
1. **WhatsApp:** adapters are built (Meta, Gupshup, Interakt, WATI, AiSensy, custom). Left to do: app/whatsapp.py calls `send_whatsapp()`, and inbound STOP handling in app/inbound.py (core branch).
2. **Inbound email:** IMAP polling and Resend / SendGrid / Postmark webhooks are built and hand messages and bounce events to app/inbound.py. Left to do: matching and suppression in app/inbound.py (core branch), and a circuit breaker above 2% bounces.
3. **Real database:** SQLite or MongoDB, with pagination and indexes. The JSON store suits one small team.
4. **Security:**
   - Rate limits and revoked tokens live in memory / the JSON file, which suits one server with one worker. Several workers would each keep their own limits.
   - Integrations hub:
     - A webhook address carries its secret as `?token=`, which server access logs record. Use the basic-auth form of the address where the provider accepts it, or run uvicorn with `--no-access-log`.
     - SMTP and IMAP server names are not SSRF-checked, so a local mail relay keeps working.
     - OAuth state is signed and expires after 10 minutes but is not single-use.
     - SendGrid's signed event webhook (ECDSA) and Twilio's request signature are not checked; those providers use the secret in the webhook address instead.
   - The custom HTTP email connector can't carry attachments, so it sends meeting invites as text only.
   - The web UI's own headers (CSP, HSTS) come from the proxy that serves `web/dist` (Caddy), not from the API.
5. **DPDP:** the consent ledger does not yet record purpose or notice version; WhatsApp/SMS "STOP" unsubscribes the lead but phone numbers are only kept on the do-not-contact list after an erasure.
6. **Scoring v2:** separate fit and intent, time decay, negative signals, and weights editable in settings.
7. **Duplicates:** fuzzy company matching, eTLD+1 domains, Gmail alias normalisation, and conflict logs.
8. **Live verification:** one live test each for Resend, Anthropic, Apollo, Hunter and Firecrawl, and for every Integrations hub provider. All request and response formats are coded from public documentation and tested with fakes only.
   - Campaigns: Apollo People API Search, Organization Search (`q_organization_job_titles`) and job postings, People Match by id, usage stats, Google Places Text Search, ZeroBounce and NeverBounce. Credits are an estimate (one per request or lookup).
   - Least certain: Resend inbound (the webhook may carry only metadata, so a body fetch may be needed), the AiSensy and MSG91 inbound payloads, Knowlarity's endpoint, the Google free/busy scope, and Cal.com API versions.
   - The custom webhook mapping is the fallback when a payload differs.
7. **Duplicates:** done for imports and the hygiene page (5.1). Still open: a full public-suffix list (only a short list of
   two-part suffixes such as co.in is built in) and nickname matching beyond initials.
8. **Live verification:** one live test each for Resend, Anthropic, Apollo, Hunter and Firecrawl. Their request and response formats are coded from documentation and tested with fakes only.
9. **More data providers** for India (for example Snov.io, Lusha, Prospeo, RocketReach), compared through the Data sources page.
10. **International rollout:** time zones per lead, local holidays, and GDPR and CAN-SPAM notices.

## 10. Run, test, contribute

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
cd web && npm install && npm run dev        # http://localhost:5173
```

Tests:
- `pytest -q`: 378 backend tests.
- `npm run e2e`: 75 browser steps, run against a throwaway `OUTREACH_DATA_FILE` with `OUTREACH_IGNORE_BUSINESS_HOURS=1`.
- `pytest -q`: 436 backend tests.
- `npm run e2e`: 74 browser steps, run against a throwaway `OUTREACH_DATA_FILE` with `OUTREACH_IGNORE_BUSINESS_HOURS=1`.
- `pytest -q`: 467 backend tests.
- `pytest -q`: 539 backend tests.
- `pytest -q`: 847 backend tests (99 for the flow builder).
- `npm run e2e`: 99 browser steps (6 for the flow builder).
- `npm run e2e`: 82 browser steps, run against a throwaway `OUTREACH_DATA_FILE` with `OUTREACH_IGNORE_BUSINESS_HOURS=1`.
- `node e2e/tour.mjs`: every page at three screen sizes.
- `node e2e/auth.mjs`: 9 login checks (API started with `OUTREACH_ADMIN_PASSWORD=e2e-password`).

Contribution rules:
- Write the test first, using fake transports.
- Keep the principles in section 4.
- Add a browser step for any new user-facing behaviour.
- Never commit `data/`, keys, or real personal data. Test fixtures use made-up names and numbers.
