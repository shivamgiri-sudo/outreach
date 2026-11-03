# QA report: tasks, activity trail, reminders, Priya flow, LinkedIn queue

Branch `qa/tasks-linkedin-priya`. Tests: `tests/qa/test_tasks-linkedin-priya.py` and
`tests/qa/test_tasks-linkedin-priya_linkedin.py` (21 tests). Fixture: `tests/qa/fixtures/tasks-linkedin-priya/team_leads_export.csv`.
Full suite after the fixes: **868 passed**.

## How it was simulated

- **Team:** Anil (owner, has an email address), Priya (caller, has an email address), Rohit (researcher, no email address), set through `PUT /api/team`.
- **Leads:** a 90-row team export (BOM, ALL CAPS and Hinglish names, "Pvt. Ltd." / "PRIVATE LIMITED" / LLP / OPC, +91/0/dashes/two numbers in one cell,
  "5+", "approx 20", "1,001-5,000", http/`in.` LinkedIn links, blank cells, duplicate rows). Imported through `/api/import/commit`:
  84 leads created, 2 duplicates. All names, domains (`*.example`) and numbers (`90000 1xxxx`) are made up.
- **Clock:** a fake `datetime` in `activities`, `inbound`, `reminders` and `routes_today`, so `now_utc()`, reply handling,
  task due times, reminders and Today all move together. LinkedIn functions take `now=` directly.
- **10 working days (Mon 2 Nov to Fri 13 Nov 2026, weekend skipped, then Mon 16 Nov):** each day there is an email yes at 10:15, a repeated yes
  from the same lead, a Hinglish WhatsApp yes at 14:40, a yes after 18:30 (19:05), and a research task for Rohit. Priya logs calls at
  11:20 (for the evening yeses) and 15:10. Reminders run every 15 minutes from 09:00 to 21:00, and also on Saturday.
  The checks: exactly one high-priority call task per yes lead, owned by Priya, due in the right slot (the Friday evening yes is due
  Monday 11:00). Each task gets one "due" reminder, plus one "overdue" reminder only when it was not called within an hour. Logging a
  call closes the task. Rohit has no email address, so he gets in-app reminders only. Priya gets one email per notification.
- **Other branches tested:** the reminder email fails (SMTP 421) but the in-app notification is still made, and the email is not retried;
  email reminders off; snooze resets the reminders; a done task gets no reminders; validation errors (400/404/422); reassigning a task;
  board buckets and the owner filter; legacy task rows (no id or status, `done: true`); the old Done-by-index endpoint; Today ordering
  (problem, then overdue task, then call, then nudge, then start); notifications unread/owner/mark-read; team input cleaning; the trail
  with a practice email (signed unsubscribe link), a pasted reply, an outgoing WhatsApp message, an incoming WhatsApp message stamped
  in IST, a call, a note with a follow-up, a meeting stored as +05:30, and tasks; the LinkedIn queue over three days at a limit of 10
  a day (30 people), waiting for acceptance, the step rhythm 2, 3, 5, 6, 7 (+2/+4/+4/+5/+7 days), the case-study step and its branch
  messages, and replies by email, WhatsApp, LinkedIn and the unsubscribe link.

## Bugs found and fixed (each in its own commit, with a regression test)

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | high | `POST /api/tasks/done` (the **Done button on Today**) set only `done: true`. The task kept `status: "open"`, so it stayed in the board's open buckets for ever. | `app/routes_today.py`: also set `status: "done"` and `done_at`. |
| 2 | medium | The activity trail sorted by **string**. Provider stamps in IST (`+05:30`, for example an incoming WhatsApp message or a meeting `created_at`) were placed above later UTC events. | `app/activities.py`: sort on the parsed time (`_when`). |
| 3 | low | A **logged note** (or an "other" activity) appeared twice in the trail: once as the activity row and again as its timeline line. | `app/activities.py`: skip timeline lines that match a logged activity's (time, type). Imported notes still show. |
| 4 | high | **LinkedIn case-study step came back**: if the https link was set after step 5, the next step became step 4 ("offer the case study") after the meeting ask. After step 7 the sequence never finished. | `app/linkedin_seq.py` `_next_step`: take only steps after the highest one done. |
| 5 | high | **Stopped LinkedIn sequence restarted**: `mark_accepted` had no ended check, so marking a replied lead as accepted put it back to "messaging" and scheduled step 2. Also, an unsubscribe-link click (or a manual move to Unsubscribed/Disqualified) did not stop LinkedIn, so the steps already scheduled kept showing in the queue. | `app/linkedin_seq.py`: the queue skips unsubscribed and disqualified leads; `mark_sent` and `mark_accepted` refuse ended sequences and closed leads. |
| 6 | high | **Reminders for unsubscribed leads**: the board drops automatic tasks once a lead unsubscribes, but the reminder timer still sent "Due/Overdue: Call them", in-app and by email. | `app/reminders.py`: same rule as the board. A person's own tasks, review tasks and erasure tasks are still reminded. |
| 7 | high (DPDP) | **The Erasure request task was invisible**. A "delete my data" reply unsubscribes the lead and creates the task, and then the board and Today hid it because the lead is unsubscribed. | `app/routes_activities.py` and `app/routes_today.py`: keep the `erasure_request` kind visible. |

## Limitations and recommendations (not changed)

- **Quick paste import drops LinkedIn links:** `POST /api/leads/import` (quick paste) keeps no `linkedin_url` or `city`, even for
  Sales Navigator style exports. Those leads cannot start the LinkedIn sequence. The smart import (`/api/import/commit`) keeps them.
  Recommendation: add `linkedin_url` to the `fill` dict in `lead_import.quick_rows` (this belongs to the import area).
- **Yes between 00:00 and 09:00 IST on a weekday:** `next_call_slot` returns "now". The call task is due at, say, 02:00, and the reminder
  (and its email) fires at night. Recommendation: move it to the start of the call window (for example 09:30, or 11:00 as for evenings).
- **LinkedIn daily limit is counted per UTC day** (`started_at[:10]`), not per IST day. This only matters between 00:00 and 05:30 IST. Recommendation: compare IST dates.
- **Message steps before acceptance:** `mark_sent(step >= 2)` is accepted while the connection is still waiting. LinkedIn cannot
  message someone who is not a connection. Recommendation: refuse message steps until `accepted_at` is set.
- **Today and the board disagree on a person's own tasks for unsubscribed leads.** The board keeps them, labelled. Today hides
  everything except erasure requests. Recommendation: use one shared rule.
- **"Mark all read" is global:** `POST /api/notifications/read` marks every person's notifications as read. With a team, it should
  take the viewer's name.
- **A task created already more than an hour overdue** (for example a legacy row) gets a "due" reminder, then an "overdue" reminder a
  minute later. This is harmless, but the "due" reminder could be skipped when the task is already overdue.
- **Today's call ordering:** call tasks that are not overdue are sorted with the other tasks by due time. Calls for "interested" leads
  that have no call task come after all tasks. This matches "problems, overdue, calls, nudges, starts" only when the call tasks are due.

## Formats

No outside file format was needed: this area takes CSV only through the import area's existing parsers. The fixture is a generic
team export in the Apollo style, with column names the header map already recognises. The LinkedIn 300-character connection-note
limit is the one the code already sets (`NOTE_LIMIT`). No outside sources were consulted.
