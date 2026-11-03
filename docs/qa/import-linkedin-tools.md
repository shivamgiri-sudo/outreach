# QA report: importing LinkedIn Sales Navigator and scraper-tool exports

Branch `qa/import-linkedin-tools` (from `main` after the exports/backup and roles merge). Not pushed.

## Scope

- **Import path:** smart import (`POST /api/import/preview` and `/commit`), covering format detection, column mapping, standardisation, dedupe and merge.
- **No-scraping check:** the page reader (`/api/scrape/extract` and `fetch_page`), to confirm the app never fetches LinkedIn itself.
- **PRD §4 principles exercised:**
  - a "no" is never a "yes": unsubscribed and suppressed people stay that way across tools;
  - closed leads stay closed;
  - only compliant sources: LinkedIn is import-only, with no automated scraping.

## Export formats researched

Vendors do not publish complete header lists. Every source below was checked on 2026-11-03. The "confidence" column says how each fixture header was obtained.

| Tool | Fixture | Source | Confidence |
|---|---|---|---|
| Waalaxy prospect-list export | `waalaxy_prospects_semicolon.csv` | https://intercom.help/waalaxy/en/articles/9858714-how-to-export-your-prospect-lists-in-a-csv-file (lists all 22 columns in order) | Exact headers. The fixture is re-saved as semicolon-separated with a BOM, as Excel does in an EU locale. |
| PhantomBuster Sales Navigator Search Export | `phantombuster_salesnav_search.csv` | https://support.phantombuster.com/hc/en-us/articles/26971086525202-How-to-Use-the-Sales-Navigator-Search-Export, https://phantombuster.com/automations/sales-navigator/6988/sales-navigator-search-export/tutorial | The camelCase result keys (`profileUrl`, `linkedInProfileUrl`, `defaultProfileUrl`, `regularCompanyUrl`, `vmid`, `pastExperience*`, `query`, `timestamp`) come from known Phantom result files. The support page returned 403 to the fetcher, so the headers are not verbatim from the docs. |
| PhantomBuster LinkedIn Profile Scraper | `phantombuster_profile_scraper.csv` | https://support.phantombuster.com/hc/en-us/articles/26971030050834-How-to-Use-the-LinkedIn-Profile-Scraper, https://support.phantombuster.com/hc/en-us/articles/32666220346130 (confirms `linkedinProfileUrl`, `firstName`, `companyName`-style keys) | Partly confirmed. `company2` / `jobTitle2` (the previous job) and `mail` are from known result files. |
| Evaboot Sales Navigator list | `evaboot_salesnav_list.csv` | https://intercom.help/evaboot/en/articles/10600662-how-does-the-sales-navigator-export-work, https://evaboot.com/ ("39 columns of profile and company data") | Reconstructed. The Title Case names are built from Evaboot's published field descriptions (cleaned names and titles, company domain and website, company LinkedIn, email and email status, years in position). |
| Dux-Soup visit export | `dux_soup_visits.csv` | Dux-Soup has no public column reference; the web search found none. | Reconstructed from known exports (`VisitTime`, `Profile`, `SalesProfile`, `Middle Name`, `CompanyProfile`, `CompanyWebsite`, `Degree`). |
| Kaspr contact export | `kaspr_contacts.csv` | https://help.kaspr.io/en/articles/8820162-how-to-use-kaspr-s-csv-enrichment (LinkedIn URL key; separate B2B and direct emails; phones) | Reconstructed column names (`LinkedIn`, `Work email`, `Direct email`, `Phone 1`, `Phone 2`). |
| lemlist lead export | `lemlist_leads.csv` | lemlist's standard lead variables (`firstName`, `lastName`, `companyName`, `companyDomain`, `linkedinUrl`, `icebreaker`, `phone`, `jobTitle`) | Fairly confident. `lastState` is the campaign status. |
| LinkedIn Recruiter "Save to CSV" | `linkedin_recruiter_project.csv` | https://support.teamable.com/article/60-importing-candidate-profiles-from-a-linkedin-recruiter-csv-file, https://www.leonar.app/blog/how-to-export-candidates-from-linkedin-recruiter/ | Reconstructed (`Headline`, `Current Title`, `Current Company`, `Profile URL`). |

La Growth Machine was not built as a fixture. Its camelCase headers (`proEmail`, `persoEmail`, `linkedinUrl`) map the same way as lemlist's; this was checked ad hoc.

## Fixtures

The fixtures are in `tests/qa/fixtures/import-linkedin-tools/`.

- **Generator:** `make_fixtures.py` builds all eight files deterministically. Re-run it with the venv Python.
- **Shared pool:** `pool.json` is the ground truth. It holds 90 fictional HR, TA and founder contacts at 30 fictional Indian employers (`*.example` domains, phones `90000 1xxxx`, LinkedIn slugs ending `-qa###`).
- **Overlap:** each tool takes a different but overlapping slice of the pool, between 40 and 91 rows.
- **Messy values included:**
  - names in ALL CAPS and all lower case, and Hinglish names ("Mohd. Irfan", "Bablu", "Chintu", "Raju Bhai");
  - company names with "Pvt. Ltd.", "PRIVATE LIMITED" and "(OPC)";
  - phones written as `+91 …`, `0…`, `91…` and `…-…`;
  - locations in the forms "City, State, India", "Greater X Area", "Mumbai Metropolitan Region" and plain "India";
  - Excel-style dates, a BOM, semicolon separators and a trailing blank row;
  - a row PhantomBuster appended twice, and a failed-profile row (`error: Can't open profile`);
  - rows carrying `javascript:` and `vbscript:` links.

## Tests

| File | What it covers |
|---|---|
| `tests/qa/test_import-linkedin-tools.py` (16 tests) | For each tool: detection, mapping, and a preview that saves nothing. Name and headline cleanup. City, State, India split. All eight tools imported in a row give exactly one record per LinkedIn profile, each filed at the person's current employer. No `javascript:`, Sales Navigator or `ACw…` links survive. Kaspr emails land on the person that Sales Navigator created. Re-importing everything a week later creates nothing. Unsubscribe and suppression hold across tools. Importing makes no network call (sockets patched to fail). |
| `tests/qa/test_import-linkedin-tools_regressions.py` (38 tests) | One regression test or test group per bug below. |

Full suite: **802 passed** (baseline 748).

## Bugs found and fixed

Each fix is its own commit with a regression test.

1. **The import crashed when a merge filled an other contact's email** (`026b32c`, `app/lead_merge.py`).
   - `merge_record` checked whether the lead's main email was a role address whenever "email" was filled. Filling an `other_contacts` entry also reports "email", so when the main contact had no email the check raised `AttributeError` and the whole import returned 500.
   - Surfaced by a Dux-Soup file followed by an Evaboot file.
2. **Previous jobs and tool-only columns became lead fields** (`03def38`, `app/header_map.py`).
   - **Previous employer as current company:** PhantomBuster's `pastExperienceCompanyName` matched the "company name" keyword rule. A lead with no current position was filed under the employer they had left.
   - **Previous job in the Profile Scraper:** `company2`, `companyUrl2` and `jobTitle2` fuzzy-matched the current company, title and company LinkedIn. `companyUrl2` even beat the website-mapped `companyUrl` for the company LinkedIn field.
   - **Dux-Soup middle name:** `Middle Name` became the full name, so "Sunita Kumari Devi" was imported as "Kumari".
   - **Wrong fields from other columns:** `profileImageUrl` became the company website (a `media.licdn.com` image became the domain). `searchAccountProfileName`, the seller's own name, was a fallback contact name. lemlist's `lastState` ("emailsSent") was read as the state.
   - **Sniffer overrode known non-lead columns:** the value sniffer re-mapped them anyway, for example Evaboot "Sales Navigator Profile URL" and "Twitter URL" to website.
   - **Fix:** named these columns as extras and exempted extras from the value sniffer.
3. **A Sales Navigator link hid the real LinkedIn link, and disguised links were rewritten** (`6804c5c`, `app/lead_import.py`, `app/standardise.py`).
   - **Real link hidden:** only the first non-empty LinkedIn column was tried. PhantomBuster's `profileUrl` holds a `/sales/lead/` link, which does not parse, so the lead got no LinkedIn at all. Now the first value that parses wins, and the public `/in/slug` is preferred over the `ACw…` member id so that the person matches other tools.
   - **Disguised links:** `linkedin_url()` searched anywhere in the text. It turned `javascript:alert(document.cookie)//www.linkedin.com/in/x` and `https://evil.invalid/?next=linkedin.com/in/x` into LinkedIn links. A link must now start with the LinkedIn host; the `m.`, `in.` and `www.` prefixes are allowed.
4. **Leads were never matched on LinkedIn profile** (`8a76962`, `app/lead_merge.py`).
   - The LinkedIn link is the one key every tool exports, but matching never used it.
   - **Duplicates:** the same profile from Sales Navigator and Kaspr, with the company written differently, became two leads.
   - **Dropped email:** when the company names did match, the result was a company-level match, and the Kaspr email was dropped.
   - **Re-import duplicates:** LinkedIn-only rows were duplicated on every re-import.
5. **The LinkedIn headline was stored whole as the title** (`94a28e6`, `app/standardise.py`, `app/header_map.py`).
   - Waalaxy's "Occupation" holds the headline, e.g. "Head HR at Punjab Agro Pvt Ltd | Ex-TCS | Hiring 100+".
   - `clean_title` now keeps the part before ` at `, `@`, ` | ` or ` • `, so that example becomes "HR Head".
   - A `headline` column is now a fallback title at 0.7 confidence and never overrides a real title column.
6. **The page reader fetched LinkedIn** (`64b1473`, `app/scraper.py`).
   - `/api/scrape/extract`, and enrichment through `fetch_page`, requested `linkedin.com/robots.txt` and then the page itself. The PRD rules out any automated LinkedIn scraping.
   - LinkedIn hosts are now refused before any network call.
7. **Kaspr files were labelled "Lusha export"** (`da46cde`, `app/header_map.py`).
   - Every Kaspr lead got source "lusha", and Dux-Soup files were an unrecognised layout. Both now have their own layout.
8. **A person location of "India" left the lead with no city** (`af65392` and `03ec8e3`, `app/lead_import.py`).
   - Sales Navigator tools export both the person's location and the company's. Only the first was read, so "India" or a bare "Kota" left the lead without a city or state.
   - Now the first location column that names a city wins. Missing state or country is taken only from a column that names the same city, so one column's city is never mixed with another column's state.

## Product limitations and recommendations (not changed)

- **Company LinkedIn link forms differ:** tools write `/company/70018` or `/company/vanity-slug`. The same employer then logs `company_linkedin_url` "conflicts", and it splits into two leads when the tools also disagree on the name ("Nilgiri Dairy" vs "Nilgiri Dairy Farm"). In the eight-tool run this gave 35 leads for 30 employers plus 2 probe rows.
  - Recommendation: use the company LinkedIn link as a company match key, and store the numeric id beside the slug when both are seen.
- **Company match with a compatible name loses the new contact details:** this happens when there is no LinkedIn link. `merge_record` with `who="company"` ignores a row whose person "is" the main contact by name. Their new email and phone are not added.
  - Recommendation: when the names are compatible, fill the main contact or other contact instead of skipping. This is shared merge logic, so it was left to the merge owners.
- **Suppression is by email only:** a LinkedIn-only row for a suppressed person, from a tool that exports no email, is still created as a lead without an email. It cannot be emailed, but it can appear in LinkedIn task lists.
  - Recommendation: on unsubscribe, also suppress the person's LinkedIn link.
- **Redirects to LinkedIn:** `fetch_page` checks only the typed host. A public page that redirects to linkedin.com is still followed by `guarded_request`.
  - Recommendation: pass a host deny-list into the redirect check.
- **Personal websites can become the company domain:** Dux-Soup's `PersonalWebsite` and the Profile Scraper's `website` (the person's own site) map to `website` and can set the company domain.
  - Recommendation: add both as extras, or map them to notes.
- **Headline trimming loses dual roles:** "HR | Admin Manager" becomes "HR". Titles rarely use `|`, so this trade-off was accepted.
- **Email verification status is ignored:** Evaboot's "Catch-all" and "Not found" values are not used.
  - Recommendation: mark catch-all addresses unverified so that sending throttles them.
- **LinkedIn Recruiter exports list candidates:** they are job seekers, not employers. The import works, but these people are the wrong audience for BookMyInterview.ai.
  - Recommendation: warn when a Recruiter layout is detected.
- **Outreach state from tools is dropped:** Waalaxy and lemlist outreach state ("Message Sent", "Profile Status", `lastState`) is not imported, so prior contact by another tool is invisible.
  - Recommendation: keep these values as a note.
- **PRD is out of date:** the §3 "Known layouts" list does not yet name Kaspr or Dux-Soup. The PRD was not edited.
