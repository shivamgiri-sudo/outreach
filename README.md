# MCN Outreach (formerly Outreach OS)

Lead management and outreach for BookMyInterview. Indian employers first, international later.

- Import and enrich leads.
- Send a short, honest email sequence.
- Let people try the first role free and track who uses it.
- Run your own rules ("when X happens, do Y"), or draw whole flows in the **Flow builder**.
- Hand warm leads to WhatsApp and Priya's call desk (PD dialling only).

Email, WhatsApp and SMS stay in practice mode ("dry_run") until providers are connected on the **Integrations** page.
Real email also needs **Send for real** ticked on the Outbox page. WhatsApp and SMS each have their own switch on their tab.

## What's inside
- **Dashboard and Today:** one ranked list of what to do next: sending problems, replies to handle, calls to make, free-trial nudges.
- **Leads:**
  - Search, filter by stage or temperature, sort by column.
  - Select several leads to start the sequence or download a CSV.
  - **Import wizard:** upload or paste any lead list, whatever its column names (Apollo, Sales Navigator, Lusha, ZoomInfo,
    Hunter, Snov.io, Naukri and other job boards, IndiaMART, JustDial, Google Maps, the team sheet or your own).
    Check the column choices, see a dry run (new, merged, duplicates, left out and why), then import. Undo is one click.
  - Every row is tidied: names, company names, job titles, emails, +91 phones, website domains, city, state, country, company size.
  - Duplicates are merged by email, Gmail address, phone, company website or a similar company name in the same city.
    Nothing filled in is overwritten; different values wait for you to choose.
  - The team Google Sheet tabs (Leads, Demo Shared, Decision Makers) can still be imported on their own, and "Quick paste" keeps the simple form.
- **Data hygiene:** what is missing or wrong, duplicate groups side by side with one-click merge, values to check,
  and a previewed clean-up of older leads.
  - Enrichment from public company websites.
  - Shown as cards on phones.
- **Lead panel:**
  - Buying signals, stage, consent proof, demo link, free pilot, referral, and WhatsApp templates (with Hinglish versions).
  - LinkedIn drafts that you send by hand.
  - Logging a reply explains in plain words what it did.
- **Campaigns** (Pipeline → Campaigns): save who to target (industries, cities, roles they hire for, company size) and
  where to look (company careers pages, Apollo people search, Apollo companies hiring, Google Places, leads you imported).
  One click finds companies, adds new leads without duplicates, fills missing contacts, checks emails, scores them and,
  only if you tick it, starts the email sequence for the ones that qualify. "Run now (preview)" saves nothing.
  - Unsubscribed, bounced and disqualified companies are never re-added; leads already in conversation are never touched.
  - The campaign never sends: the sequence engine does, with practice mode, caps, warm-up and business hours.
  - Priya (or the campaign's caller) gets an update after every run, and a morning summary at 09:30 IST on weekdays.
  - "Run every weekday automatically" runs it once a day with `OUTREACH_AUTORUN=1`.
- **Flow builder** (Outreach → Flow builder): draw a flow like a mind map: a trigger (added by hand, new lead, reply,
  stage change, meeting booked, product activity, no reply for N days), then If / A-B split / Wait / Wait for a reply, then
  email, WhatsApp, SMS, LinkedIn task, call task, meeting link, stage, tag, notify, webhook or stop.
  - Three starter templates (the cold email sequence, yes → WhatsApp + Priya call, LinkedIn + email). Simulate on a lead
    before activating; nothing is saved or sent.
  - Every message keeps the app's rules: practice mode, caps, warm-up, business hours, one email per company a day,
    consent before WhatsApp and SMS, the do-not-contact list, stop on reply. Editing an active flow makes a new version.
  - Steps run every minute with `OUTREACH_AUTORUN=1`. Only owners can change flows. "Simple rules" is the old Workflows page.
- **Data sources:**
  - Run several providers on the same Indian leads and compare coverage and cost before keeping one. Providers: own website crawler, Hunter, Firecrawl, Apollo.
  - Import the JSON output of the open-source website contact scraper.
- **Reading replies:**
  - Rules first. They handle negation ("not interested", "I'm not sure"), stop words in English and Hinglish, auto-replies and bounces.
  - When the AI writer is connected, it reads only replies the rules can't place. An AI "yes" is never treated as consent; a person checks it.
- **Sending safety:**
  - One run at a time, and each send is claimed before it goes out, so nothing is sent twice.
  - The sequence is never restarted, and cold emails stop once a lead is warm.
  - At least 2 days between steps, a few sends per run, and one email per company domain a day.
  - Warm-up ramp: 10, then 20, then 35 a day.
  - Failed sends back off and stop after 3 tries.
  - Nothing goes out with an unfilled placeholder.
- **Copy:**
  - Short, link-free first email, Hinglish WhatsApp options and an honest call script. No invented customer stories.
  - AI prompts treat anything a lead writes as data, never as instructions.
- **Integrations hub** (Admin → Integrations):
  - One tab per channel: email sending, reply capture, bounces, WhatsApp, SMS, calls, calendar & meetings, lead data, AI.
  - Every provider has plain setup steps (India specifics such as DLT for SMS), a test button and "make active".
  - Providers:
    - Email: Resend, SendGrid, Postmark, Amazon SES (SMTP), any SMTP, Gmail.
    - Replies: IMAP, Resend / SendGrid / Postmark inbound.
    - Bounces: Resend / SendGrid / Postmark events.
    - WhatsApp: Meta Cloud API, Gupshup, Interakt, WATI, AiSensy.
    - SMS: MSG91, Twilio, Fast2SMS.
    - Calls: click-to-call link, Exotel, Knowlarity, Twilio Voice.
    - Calendar: Google (Meet), Microsoft 365 (Teams), Zoom, Cal.com, Calendly, any booking link.
  - **Custom connector** for any other provider: URL, headers, body template with `{{placeholders}}`, and a test showing the request and reply. A **custom webhook** maps any provider's payload.
  - Webhook addresses to paste into provider dashboards are listed with copy buttons. They carry a secret, and calls without it (or without the provider's signature) are refused.
- **Meetings:**
  - "Book a meeting" in the lead panel creates the invite with a video link, or gives a prefilled booking link.
  - Zoom, custom calendar APIs and a booking link with a fixed time get an `.ics` calendar invite emailed by the app (Google and Microsoft send their own; tick "Send our own invite" to add ours). Cancelling sends the cancellation.
  - Reminders carry the join link; 30 minutes after a meeting ends without "Mark done", the owner gets a follow-up task.
  - Cal.com and Calendly bookings land on the matching lead.
  - A Meetings page lists upcoming meetings in IST.
- **Compliance (India's DPDP Act):**
  - Consent is recorded before WhatsApp, and every consent given or withdrawn goes into an append-only consent ledger.
  - Every message has a signed unsubscribe link; opening it asks for a click, so email link-scanners can't unsubscribe people.
  - Owners can download everything held on a person or erase it (lead panel, or the API below). After erasure only a hash of the email and phone is kept, so the person is never imported or contacted again.
  - A reply asking to delete their data creates an "Erasure request" task. Cold leads past the review period (default 365 days) are listed for review, never deleted automatically.
  - An optional privacy notice link goes in every email footer. Suppression list, robots.txt respected, no LinkedIn scraping or automation.
- **Logins and roles:**
  - The admin password signs in as owner. Owners add team logins on the Setup page: **owner** (everything), **caller** (leads, calls, meetings, tasks; no settings, integrations, workflows, live switches or CSV exports) or **viewer** (read only).
  - Sessions last 12 hours and renew themselves while in use; "Sign out" ends them on the server; changing a password signs out old sessions.
- **Security:**
  - Rate limits on login, unsubscribe, demo and webhook addresses; security headers on every response; API docs off by default.
  - Every address the server fetches is checked against private and internal networks, and the connection is pinned to the checked IP.
  - Saved keys are encrypted at rest when `OUTREACH_SECRET_KEY` is set.
- **Downloads and backup:**
  - Every list has a download button (CSV that opens in Excel with Indian names and ₹ intact, or JSON): leads, emails,
    replies, WhatsApp, reminders, tasks, a lead's full activity, campaign leads and runs, meetings, duplicates, insights,
    the LinkedIn queue. Times come in IST plus the raw value; cells can't run as spreadsheet formulas; no keys ever.
  - **Setup → Backup & restore:** one `.json.gz` with everything (keys left out unless you ask), and a restore that
    shows what would change first, keeps the old file and leaves sending off. `scripts/backup.py` does the same on a
    server. Moving to your own server or a laptop: [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).
- **Compliance:**
  - Consent is recorded before WhatsApp. Every message has an unsubscribe.
  - Opening the unsubscribe link asks for a click, so email link-scanners can't unsubscribe people.
  - There is a suppression list, robots.txt is respected, and no LinkedIn scraping or automation.
- **Chrome assistant (assisted, not automated):** `extensions/linkedin-assistant/` is a Manifest V3 Chrome extension
  (plain JS, no build step). On a LinkedIn profile you have open it finds the matching lead, shows the ready-made
  message, and can copy it or insert it once into the open note / message box when you press its button. **You press
  LinkedIn's Send yourself**, then confirm "I sent it / They accepted / They replied" in the panel. It never clicks,
  scrolls, scrapes, crawls or sends for you (LinkedIn forbids automation); `tests/test_extension_rules.py` fails if such a
  call ever appears in its source. Pair it with a token made on the LinkedIn outreach page (shown once, stored hashed,
  revocable, valid only for `/api/extension/*`). Install steps: [extensions/linkedin-assistant/README.md](extensions/linkedin-assistant/README.md).

## Run
    .venv\Scripts\python -m uvicorn app.main:app --port 8000
    cd web && npm run dev            # http://localhost:5173  (Ctrl+K = quick search)

Optional environment variables:
- `OUTREACH_ADMIN_PASSWORD`: turns on the login screen. **Without it the API only answers on this computer.**
- `OUTREACH_AUTORUN=1`: sends due emails and runs time-based rules every 15 minutes (Mon–Fri 9–18 IST), runs campaigns set to
  run daily (once a day), checks the reply mailbox (IMAP) every 5 minutes, and sends reminders and the 09:30 IST summary.
- `OUTREACH_DATA_FILE`: where data is stored. Default `data/store.json` (git-ignored). Saves are atomic, and saved keys are in plain text.
- `OUTREACH_ADMIN_PASSWORD`: turns on the login screen (this login is an owner). **Without it (and without team logins) the API only answers on this computer.**
- `OUTREACH_SECRET_KEY`: encrypts saved API keys, passwords and tokens in the data file. Use a long random value, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`, and keep it safe: without it the saved keys can't be read. Plain keys are converted on the next start.
- `OUTREACH_ENABLE_DOCS=1`: turns on `/docs` and `/openapi.json` (off by default).
- `OUTREACH_ALLOW_PRIVATE_HOSTS`: comma-separated `host:port` the server may fetch even though it is private, e.g. `localhost:3002` for a self-hosted Firecrawl.
- `OUTREACH_AUTORUN=1`: sends due emails and runs time-based rules every 15 minutes (Mon–Fri 9–18 IST), and checks the reply mailbox (IMAP) every 5 minutes.
- `OUTREACH_DATA_FILE`: where data is stored. Default `data/store.json` (git-ignored). Saves are atomic; saved keys are encrypted only when `OUTREACH_SECRET_KEY` is set.
- `OUTREACH_WEBHOOK_SECRET`: header `X-Webhook-Secret` that lets the BookMyInterview product call `POST /api/activation/event`. It can also be set in Setup.
- `OUTREACH_BASE_URL`: public address for demo and unsubscribe links. It can also be set in Setup.
- `OUTREACH_LLM_MODEL`: model used for AI reply drafts and reply reading. Default `claude-sonnet-5-5`.
- `OUTREACH_UI_ORIGINS`: extra web addresses (comma-separated) the calendar sign-in may return to, if the UI is not served from the public address.
- `OUTREACH_IGNORE_BUSINESS_HOURS=1` and `OUTREACH_TEST_MODE=1`: local testing only.

### Deploying (one server behind Caddy)
Run one worker on the local address and let Caddy add HTTPS:

    uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

Caddy's `reverse_proxy` sends `X-Forwarded-For` and `X-Forwarded-Proto`; the app trusts them only from 127.0.0.1 (rate limits, HSTS). Rate limits and sign-outs are kept per process, so use one worker. Serve `web/dist` from Caddy and proxy `/api/*`, `/u/*` and `/demo/*` to the app; set headers for the web UI itself in Caddy.

### DPDP endpoints (owner only)
- `GET /api/leads/{id}/export`: everything held on the person, as JSON.
- `POST /api/leads/{id}/erase` with `{"confirm": true}`: deletes it, keeps only hashes so they are never re-imported or contacted.
- `GET /api/compliance/retention`: cold leads past `retention_days_cold` (Setup), for review.
- `GET /api/compliance/consent-ledger?lead_id=…`: consent given and withdrawn.

## Test
    .venv\Scripts\python -m pytest -q          # 378 backend tests
    .venv\Scripts\python -m pytest -q          # 436 backend tests
    .venv\Scripts\python -m pytest -q          # 467 backend tests
    .venv\Scripts\python -m pytest -q          # 539 backend tests
    .venv\Scripts\python -m pytest -q          # 915 backend tests (99 flow builder, 68 Chrome assistant)

Browser walk-through (start the API with `OUTREACH_DATA_FILE` pointing at a throwaway file and
`OUTREACH_IGNORE_BUSINESS_HOURS=1`, then the UI):

    cd web && npm run e2e                      # 75 steps; HEADED=1 shows the browser
    cd web && npm run e2e                      # 74 steps; HEADED=1 shows the browser; E2E_UI / E2E_API change the addresses
    cd web && npm run e2e                      # 82 steps; HEADED=1 shows the browser
    cd web && npm run e2e                      # 99 steps (6 flow builder)
    node e2e/tour.mjs                          # every page at desktop, tablet and phone width
    # with the API started using OUTREACH_ADMIN_PASSWORD=e2e-password:
    node e2e/auth.mjs                          # 9 login steps

## Not verified against live services
The Chrome assistant has not been run inside a real Chrome against the real linkedin.com. Its LinkedIn page selectors
(name, company, note box, message box) will need maintenance when LinkedIn changes its pages; see its README.

Resend (email), Anthropic (reply drafts and reading), Apollo (match, People API Search, Organization Search, job
postings, usage stats), Hunter, Firecrawl, Google Places, ZeroBounce, NeverBounce and every Integrations hub provider
(email, inbound, WhatsApp, SMS, calling, calendars) are coded from public documentation and tested with fake transports
only. Try each once with a test address or a small sample before relying on it.

`app/inbound.py` in this branch is a stub. Replies and bounces are captured and normalised, but matching them to leads
comes from the core branch.
