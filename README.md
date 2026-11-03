# Outreach OS

Lead management and outreach for BookMyInterview. Indian employers first, international later.

- Import and enrich leads.
- Send a short, honest email sequence.
- Let people try the first role free and track who uses it.
- Run your own rules ("when X happens, do Y").
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
  - Cal.com and Calendly bookings land on the matching lead.
  - A Meetings page lists upcoming meetings in IST.
- **Compliance:**
  - Consent is recorded before WhatsApp. Every message has an unsubscribe.
  - Opening the unsubscribe link asks for a click, so email link-scanners can't unsubscribe people.
  - There is a suppression list, robots.txt is respected, and no LinkedIn scraping or automation.

## Run
    .venv\Scripts\python -m uvicorn app.main:app --port 8000
    cd web && npm run dev            # http://localhost:5173  (Ctrl+K = quick search)

Optional environment variables:
- `OUTREACH_ADMIN_PASSWORD`: turns on the login screen. **Without it the API only answers on this computer.**
- `OUTREACH_AUTORUN=1`: sends due emails and runs time-based rules every 15 minutes (Mon–Fri 9–18 IST), and checks the reply mailbox (IMAP) every 5 minutes.
- `OUTREACH_DATA_FILE`: where data is stored. Default `data/store.json` (git-ignored). Saves are atomic, and saved keys are in plain text.
- `OUTREACH_WEBHOOK_SECRET`: header `X-Webhook-Secret` that lets the BookMyInterview product call `POST /api/activation/event`. It can also be set in Setup.
- `OUTREACH_BASE_URL`: public address for demo and unsubscribe links. It can also be set in Setup.
- `OUTREACH_LLM_MODEL`: model used for AI reply drafts and reply reading. Default `claude-sonnet-5-5`.
- `OUTREACH_UI_ORIGINS`: extra web addresses (comma-separated) the calendar sign-in may return to, if the UI is not served from the public address.
- `OUTREACH_IGNORE_BUSINESS_HOURS=1` and `OUTREACH_TEST_MODE=1`: local testing only.

## Test
    .venv\Scripts\python -m pytest -q          # 378 backend tests
    .venv\Scripts\python -m pytest -q          # 436 backend tests

Browser walk-through (start the API with `OUTREACH_DATA_FILE` pointing at a throwaway file and
`OUTREACH_IGNORE_BUSINESS_HOURS=1`, then the UI):

    cd web && npm run e2e                      # 75 steps; HEADED=1 shows the browser
    cd web && npm run e2e                      # 74 steps; HEADED=1 shows the browser; E2E_UI / E2E_API change the addresses
    node e2e/tour.mjs                          # every page at desktop, tablet and phone width
    # with the API started using OUTREACH_ADMIN_PASSWORD=e2e-password:
    node e2e/auth.mjs                          # 6 login steps

## Not verified against live services
Resend (email), Anthropic (reply drafts and reading), Apollo, Hunter, Firecrawl and every Integrations hub provider
(email, inbound, WhatsApp, SMS, calling, calendars) are coded from public documentation and tested with fake transports
only. Try each once with a test address or a small sample before relying on it.

`app/inbound.py` in this branch is a stub. Replies and bounces are captured and normalised, but matching them to leads
comes from the core branch.
