# QA: Campaigns pipeline and discovery sources

Branch `qa/campaigns-sources`. Tests: `tests/qa/test_campaigns-sources.py` (provider adapters),
`tests/qa/test_campaigns-sources_runs.py` (whole runs through the API), `tests/qa/test_campaigns-sources_regressions.py`
(one test per bug fixed). Fixtures: `tests/qa/fixtures/campaigns-sources/`. All data is made up: `*.example` domains,
`-example` social handles, mobiles `+91 90000 xxxxx`, landlines `+91 20 0612 xxxx` (no real Indian landline starts with 0
after the area code). Every external call goes through `tests/campaign_fakes.FakeIO`; nothing reaches the network.

## Formats researched

| Source | What the fixture copies | Reference |
|---|---|---|
| Apollo People API Search `POST /api/v1/mixed_people/api_search` | `total_entries` + `people[]`; each person has `first_name`, `last_name_obfuscated` (`Sh***a`), `title`, `has_email: false`, `has_direct_phone: "Maybe: ..."`, `organization` with `name` and `has_*` flags only (no id, no domain, no email, no LinkedIn URL). 141 people over 2 pages, one with no organisation, one repeated across pages, 10 companies with 4 HR people each. | https://docs.apollo.io/reference/people-api-search |
| Apollo Organization Search `POST /api/v1/mixed_companies/search` | `breadcrumbs`, `partial_results_only`, `pagination {page, per_page, total_entries, total_pages}`, both `accounts[]` and `organizations[]`, `primary_phone {number, source, sanitized_number}`, `sanitized_phone`, `website_url: null` with `primary_domain`, Facebook page as website, one nameless org. | https://docs.apollo.io/reference/organization-search |
| Apollo Organization Job Postings `GET /api/v1/organizations/{id}/job_postings` | `organization_job_postings[]` with `title`, `url`, `city`, `state`, `country`, `posted_at`, `last_seen_at`; empty lists; 15 postings with some `posted_at: null`. 1 credit per page. | https://docs.apollo.io/reference/organization-jobs-postings |
| Apollo People Match `POST /api/v1/people/match` (by `id`, `reveal_personal_emails: false`) | `{request_id, person}`; 42 business emails, 6 `email_not_unlocked@domain.com`, 6 `email: null`, 6 Gmail addresses, `email_status: guessed/verified`. | https://docs.apollo.io/reference/people-enrichment |
| Apollo errors | 401 `Invalid access credentials.`, 422 `{"error": ...}`, 429 "maximum number of api calls allowed ... 600 times per hour". | https://docs.apollo.io/reference/rate-limits |
| Google Places Text Search (New) `POST https://places.googleapis.com/v1/places:searchText` | `places[]` with `id`, `displayName {text, languageCode}`, `formattedAddress`, `nationalPhoneNumber`, `internationalPhoneNumber`, `websiteUri`, `businessStatus` (one `CLOSED_PERMANENTLY` per query), `googleMapsUri`; `nextPageToken`; 6 queries x 20. Errors in the Google RPC shape: 400 `API_KEY_INVALID`, 403 `PERMISSION_DENIED` (API disabled), 429 `RESOURCE_EXHAUSTED`. | https://developers.google.com/maps/documentation/places/web-service/text-search , https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places , https://cloud.google.com/apis/design/errors |
| ZeroBounce validate `GET /v2/validate` | full reply (`status`, `sub_status`, `free_email`, `mx_found`, `processed_at` ...); statuses valid / invalid / catch-all / unknown / spamtrap / abuse / do_not_mail; `{"error": "Invalid API key or your account ran out of credits"}`. | https://www.zerobounce.net/docs/email-validation-api-quickstart/ |
| NeverBounce single check `GET /v4/single/check` | `{status: success, result, flags, suggested_correction, execution_time}`; failures `auth_failure`, `throttle_triggered`, `temp_unavail` (HTTP 200, no result). | https://developers.neverbounce.com/reference/single-check |
| Careers-page JSON-LD | schema.org `JobPosting`. Structures copied from public pages: Lever-hosted (`jobs.lever.co/<co>/<id>`: org name + logo, null PostalAddress fields, HTML description), Workday (`<tenant>.wd5.myworkdayjobs.com`: entity code in org name, `sameAs: ""`, `"Country, City"` locality), WordPress + Yoast `@graph` with `sameAs` lists, LinkedIn `sameAs`, Google's example with raw line breaks from a hand-made template. | https://schema.org/JobPosting , https://developers.google.com/search/docs/appearance/structured-data/job-posting |

## What was simulated

- **Dry run vs real run** on all four sources at once (99 Apollo people + 30 hiring companies + 4 careers companies +
  100 Places = 233 rows, 38 credits): the dry run leaves leads, jobs, campaigns, notifications, tasks, outbox and
  suppression untouched and spends no enrichment credit; the real run adds exactly the previewed number, one lead per
  company, every lead tagged with `campaign_id`, a provenance entry per field and an "Added by campaign" timeline event.
- **Dedupe**: existing leads typed in ALL CAPS, unsubscribed, disqualified and contacted, plus a company-only early lead;
  the closed and later-stage leads are byte-for-byte unchanged and never tagged; the next-day re-run adds nothing.
- **Enrichment** (People Match by Apollo id): locked emails never stored, emails only fill empty fields, nothing is
  looked up or verified twice on later runs.
- **Verification + qualification**: ZeroBounce invalid / catch-all / unknown / spamtrap block qualification, free-mail
  never qualifies; a NeverBounce outage leaves the email unverified and the next run verifies it.
- **Auto-enrol**: goes through `enroll_lead`; a lead who unsubscribes through the signed `/u/` link is not mailed, not
  re-enrolled by the next run and refused by the button; the sequence engine still applies the per-run cap, practice
  mode and one email per domain per day; the campaign itself sends nothing.
- **Autorun + digest over two weeks** (ticks every 2 h, 07:05-21:05 IST): each running campaign runs once per weekday
  at the first business-hours tick, never at weekends; the 09:30 IST digest goes out once per weekday; one caller
  notification per run.
- **Provider errors**: Apollo 401/403/422/429, job postings 429/timeout/403, Places 400/403/429 mid-run, verifier key
  and throttle errors.

## Bugs found and fixed

| # | Severity | Bug | Fix (commit) |
|---|---|---|---|
| 1 | High | Careers pages on a shared host (jobs.lever.co, myworkdayjobs.com, ...) or with a LinkedIn/social `sameAs` got that host as the company domain. Postings of different employers with the same title and city then shared one job key and **collapsed into one company** (the second was counted as a "repost"); the ATS host also became a dedupe domain. A `sameAs` **list** (Yoast, many plugins) crashed the whole run with `AttributeError`; JSON-LD with raw line breaks in a string was skipped. | `app/jobs.py`: `job_domain` skips shared/social hosts and reads `sameAs` lists; `json.loads(strict=False)` (10bafe2) |
| 2 | High | A verifier outage (NeverBounce `temp_unavail`, HTTP 200 with no `result`; or an empty ZeroBounce reply) was stored as **`risky`**, which is cached on the lead, so the lead never qualified again even after the service recovered. | `verify_email` raises `OSError` when there is no verdict, so the email stays unverified (28cbaf7) |
| 3 | Medium | Apollo hiring companies: a 403 on job postings (plan without that endpoint) raised `KeyRejected` and **discarded every company** the already-paid Organization Search returned. | stop asking after the first refusal, keep the rows, report failed lookups (7fb1a0d) |
| 4 | Medium | Google Places: a 429/outage on a later text search **discarded the results of the searches already billed**. | return rows so far with an `error`; `discover` adds it to the run report (c484b22) |

## Product limitations (not changed) and recommendations

1. **Places `nextPageToken` is never followed**: at most 20 places per "industry in city" query. Recommend following up
   to 2 extra pages when `max_results` is not reached.
2. **Apollo People API Search returns no domain or organisation id**, so people rows merge with other sources by
   normalised company name only, and People Match's `organization.primary_domain` is not used to fill the lead's empty
   domain. Recommend filling `domain`/`website` from the match when empty (it would also enable the website crawler).
3. **People Match may return a personal Gmail** even with `reveal_personal_emails: false`; it is stored on a lead that
   has no domain (qualification correctly blocks it, but the free-mail address sits on the lead). Recommend not storing
   free-mail emails from Apollo matches.
4. **Merge into an existing early lead only happens when the campaign brings an email** the lead lacks; a new phone or
   website for an email-less lead is counted as a duplicate and dropped. Recommend also filling phone/website.
5. **JobPosting `hiringOrganization.url` is ignored when `sameAs` is present** (`sameAs or url`), so a page whose
   `sameAs` is only a LinkedIn link and whose `url` is the own site gets no domain. Recommend trying both.
6. **Workday entity names** ("IN02 Sigma Fintech Services Bengaluru") are used verbatim as company names; they will not
   match the same company from Apollo/Places. Recommend stripping a leading entity code.
7. **Suppression in `enroll_lead` is checked on the read copy only**, not again inside the atomic update (stage and
   sequence are). An address suppressed in the same millisecond could still be enrolled; the sequence engine's own
   suppression check before sending still protects the message.
8. A malformed provider reply (for example `displayName` as a plain string) raises outside the caught exception types
   and aborts that tick's `run_due_campaigns`; the campaign's `last_run_date` is already set, so it waits a day.
   Recommend catching `Exception` per campaign in `run_due_campaigns` and recording it as a run error.

## Results

`tests/qa`: 45 tests. Full suite (`python -m pytest -q`): 892 passed, 0 failed.
