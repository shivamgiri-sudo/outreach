# QA: a day in the life, through the real UI (Playwright)

Script: `web/e2e/journey.mjs`. API-side replay in pytest: `tests/qa/test_browser-journey.py`.
Fixtures (all fictional, `*.invalid` domains, `90000 1xxxx` numbers): `tests/qa/fixtures/browser-journey/`.

## How to run

```bash
cd web && npm install
JOURNEY_PYTHON=/path/to/venv/bin/python node e2e/journey.mjs      # both widths, about 5 minutes
JOURNEY_WIDTHS=phone JOURNEY_VERBOSE=1 node e2e/journey.mjs        # one width, prints each step as it finishes
JOURNEY_STOP_ON_FAIL=1 ...                                         # stop at the first failure and print the page text
HEADED=1 ...                                                       # watch it
```

The script starts its own servers, so nothing else has to run:

- **API**: `uvicorn app.main:app` on `E2E_API` (default `127.0.0.1:8801`) with `OUTREACH_ADMIN_PASSWORD=journey-pass`,
  `OUTREACH_IGNORE_BUSINESS_HOURS=1` and a fresh throwaway `OUTREACH_DATA_FILE` in a temp folder for each width
  (deleted afterwards). `OUTREACH_AUTORUN`, `OUTREACH_TEST_MODE`, `OUTREACH_SECRET_KEY` and `OUTREACH_BASE_URL` are
  removed from the environment, so the app is in practice mode and nothing can leave it. If something already answers on
  the port the script stops, because it would otherwise run against that server's old data. (This happened once during
  this QA run after a killed run left uvicorn behind.)
- **UI**: Vite on `E2E_UI` (default `127.0.0.1:5201`), started through **Vite's JS API** (`createServer`) with the
  project's `vite.config.js` plus an inline `server.proxy` override that sends `/api`, `/u` and `/demo` to `E2E_API`
  instead of the default `127.0.0.1:8000`. `vite.config.js` is not changed and no temporary config file is written.
  `strictPort: true` makes a busy port an error rather than a silent move to another port.
- Screenshots: `web/journey-shots/<desktop|phone>/NN-step.png` (git-ignored). Widths: 1366x900 and 390x844.
- Checks at every step: no `console.error`, no page errors, no HTTP 4xx/5xx except refusals the step causes on purpose
  (wrong password 401, caller 403s, disqualified-lead enrol 400). At phone width, no step may leave the page scrolling
  sideways; when it does, the error names the box that is too wide.

## Result (final run, after the fixes below)

| # | Step | Desktop | Phone |
|---|------|---------|-------|
| 1 | Owner: wrong password refused ("Wrong password"), right one opens the empty dashboard | PASS | PASS |
| 2 | Import 1, Apollo CSV upload: recognised as Apollo, Apollo ids not imported, dry run of 133 rows = 108 new / 23 in-file duplicates / 2 left out, left-out CSV downloads, foreign number noted, import | PASS | PASS |
| 3 | Import 2, Evaboot Sales Navigator CSV (`;`, UTF-8 BOM): BOM does not break the first header; the "Sales Navigator URL" column is guessed as **website** and corrected to Don't import; 41 new / 12 merged by email; 6 differing values sent to Data hygiene | PASS | PASS |
| 4 | Import 3, team Google Sheet Leads tab pasted (tab separated, `28-10-2026` / `1-Nov-26` dates, two numbers in a cell): 32 new / 10 merged, 4 shared inboxes noted; 181 leads; phones standardised to +91 | PASS | PASS |
| 5 | Data hygiene: 2 duplicate groups (same phone), 1 merged, a differing value kept | PASS | PASS |
| 6 | Campaign wizard: chips, careers page HTML pasted (site blocks reading), 1 lead picked, caller prefilled, preview shows "Would add" rows and saves nothing; campaign kept as a draft | PASS | PASS |
| 7 | Leads: 13 selected, sequence started for 12; the disqualified lead is refused | PASS | PASS |
| 8 | Outbox: 4 practice runs send 5 / 5 / 2 / 0 (spread out), 12 emails, no lead gets Email 1 twice, every body has a signed `/u/<id>.<sig>` unsubscribe link and no `{{` | PASS | PASS |
| 9 | Replies pasted on 11 leads (9 kinds, English and Hinglish): only "interested" records consent; every other kind stops the sequence without consent; no WhatsApp goes out | PASS | PASS |
| 10 | Today: Priya's call task, question / wrong person / read-a-reply tasks, **and the erasure request** | PASS (after fix 1) | PASS |
| 11 | Tasks: the call task is Priya's; the erasure request is listed | PASS (after fix 1) | PASS |
| 12 | Lead panel: log a connected call with a follow-up; the call task closes, the follow-up task opens | PASS | PASS |
| 13 | Integrations: connect a booking link, test says "Works", provider Active | PASS | PASS |
| 14 | Meetings: prefilled booking link (name + email encoded), copied to clipboard; fixed-time booking tomorrow 15:00 IST with a practice invite; listed on Meetings | PASS | PASS |
| 15 | LinkedIn queue: start, copy (note under 300 characters, starts "Hi Arjun"), mark sent, they accepted; step 2 scheduled | PASS | PASS |
| 16 | Setup: owner adds a caller login | PASS | PASS (after fix 4) |
| 17 | Caller signs in: no Team logins, no basics form, no DPDP actions, no Apollo CSV; logs a call; run sequences and suggested rules show "Your role (caller) cannot do this"; export / erase / settings / users API calls return 403 | PASS (after fix 3) | PASS |
| 18 | Caller signs out (token revoked: 401), owner signs back in | PASS | PASS |
| 19 | DPDP: download the lead's data (has email and reply), erase it, it is gone from Leads, the erasure task closes, a re-import of the same person is **left out in the dry run** | PASS (after fix 2) | PASS |
| 20 | Sign out: login screen, owner token revoked | PASS | PASS |

**20/20 at both widths, no console errors and no unexpected HTTP errors.**

## Bugs found and fixed (each its own commit, smallest change)

1. **Erasure requests were invisible (DPDP).** A "delete my data" reply always unsubscribes the lead, and both
   `/api/today` (`app/routes_today.py`) and `/api/tasks/board` (`app/routes_activities.py`) drop automatic tasks of
   unsubscribed leads. So the high-priority, 24-hour "Erasure request" task never appeared in Today or on the Tasks page.
   Fix: those filters keep `erasure_request` tasks. Other tasks of unsubscribed leads stay hidden (closed leads stay
   closed). Regression: `test_an_erasure_request_shows_on_today_even_though_the_lead_is_unsubscribed`.
2. **The import dry run promised to re-add an erased person.** After erasure only hashes are kept. The store guard
   correctly dropped the row on save, but the preview counted it as "1 new" and the commit then silently added nothing.
   Fix: `app/routes_import.py` adds emails in the file whose hash is on the erased list to the do-not-contact set for
   preview and commit, so the row shows under "Left out". Regression:
   `test_erasing_the_lead_clears_the_erasure_request_and_a_reimport_is_left_out`.
3. **Caller clicking "Add suggested rules" got no feedback.** `web/src/pages/Workflows.jsx` awaited the API without a
   catch, so the 403 became an unhandled promise rejection (a page error) and nothing was shown. It now shows the
   refusal message. Covered by journey step 17.
4. **Setup scrolled sideways on phones by 111px.** The Firecrawl steps contain the unbreakable token
   `OUTREACH_ALLOW_PRIVATE_HOSTS=localhost:3002.`. `.steps li` now has `overflow-wrap: anywhere`
   (`web/src/styles.css`). Covered by journey step 16 at 390px.

Script-only fixes (not app bugs): the login label is now "Work email" after the login redesign; "Workflows" is now
"Simple rules" in the menu; the Outbox count is read from the `/api/sequences/run` answer, because the status line
still shows the previous run's text until the answer arrives (the old script read stale text and got 5,5,5,5).

## Product limitations and recommendations (not changed)

- **Sales Navigator URL is guessed as the company website.** Evaboot-style exports put a `linkedin.com/sales/lead/...`
  link first. The header map treats "URL" as website, so the user has to correct it by hand (the journey does).
  Recommendation: map `Sales Navigator URL` / `Sales Nav URL` to ignore, or to `linkedin_url` only when it is a `/in/`
  link.
- **Erased people with no email in the new file.** Fix 2 checks emails only. A phone-only re-import of an erased person
  still shows as "new" in the dry run (the store guard still drops it on save). Recommendation: also check normalised
  phone hashes in the preview if this comes up.
- **The Outbox status line keeps the previous result** while a run is in progress. A user who clicks twice quickly can
  read the old count. Recommendation: clear the message or show "Running…" while waiting.
- The journey's default Python is `<repo>/.venv`. In worktrees without one, set `JOURNEY_PYTHON`.

## Formats used for the fixtures

- **Apollo contacts export** (quoted CSV, `First Name, Last Name, Title, Company, Company Name for Emails, Email, Email
  Status, ... Person Linkedin Url, Website, Company Linkedin Url, City, State, Country, Apollo Contact Id, Apollo Account
  Id`). Built from the column names in Apollo's contact CSV documentation and community exports. Search could not find
  an official header list for Apollo's own export:
  https://knowledge.apollo.io/hc/en-us/articles/4409161532045-Upload-a-CSV-of-Contacts-to-Apollo
- **Evaboot / Sales Navigator export** (Excel-style `;` separator with a UTF-8 BOM, `Sales Navigator URL; Linkedin URL;
  First Name; ... Company Domain; Email; Email Status`). Based on Evaboot's description of its Sales Navigator export
  (verified email, valid / catch-all status, profile and company columns). The exact header names could not be checked
  against a live export: https://intercom.help/evaboot/en/articles/10600662-how-does-the-sales-navigator-export-work
- **Team Google Sheet, Leads tab** (copied cells are tab-separated, including a duplicated `Remarks` header, `DD-MM-YYYY`
  and `D-Mon-YY` dates, and `/`-separated numbers). These follow the team sheet layout the importer's "Team sheet"
  format already recognises (`app/header_map.py`).
