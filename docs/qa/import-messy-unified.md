# QA report: import of messy files and one unified list from several sources

Branch `qa/import-messy-unified`. Tests: `tests/qa/test_import-messy-unified.py` (messy files, team sheet tabs) and
`tests/qa/test_import-messy-unified_unified.py` (six-source unified list). Fixtures: `tests/qa/fixtures/import-messy-unified/`.
All data is made up: `*.example` domains, `90000 1xxxx` numbers, invented names.

Full suite after this work: **777 passed** (baseline 748, 29 tests added).

## What was tested

| Area | Result |
|---|---|
| UTF-8 BOM, CRLF, semicolons, quoted newlines, `""` quotes, trailing `;;` columns, blank rows (Excel "CSV UTF-8") | Works. The BOM never reaches a header, the blank columns become "Column 10/11" and are ignored, and a newline inside a quoted cell stays in one note |
| Tab-delimited file | Works |
| UTF-16 (Excel "Unicode Text") | The browser's `File.text()` always decodes as UTF-8, so the server gets text full of NULs. **Was:** a cryptic "line contains NUL" error. **Now:** a clear message saying to save the file as CSV UTF-8 (fixed). A client that decodes the file itself imports fine |
| Excel serial dates (`46318`) and date-times (`28/10/2026 10:30`) in "Posted On" | **Was:** job age 0, so the "fresh job" signal was lost. **Now:** both are read (fixed) |
| Duplicate header names (`Email,Email,Phone,Phone`) | Works: they become `Email (2)` / `Phone (2)` and both values are kept |
| Merged two-row header (group titles above the real names) | **Was:** the group-title row became the header, the real header row became data, and the whole file was misread. **Now:** that layout is detected and the second row is used as the header (fixed) |
| 5,000-row Apollo-style file (1,667 companies × 3 people) | Preview ≈ 5 s, commit ≈ 5 s, both well inside the 30 s test limit. 3,333 people go to other_contacts, response lists are capped at 500 items, and re-importing changes nothing. More than 20,000 rows is refused |
| `=HYPERLINK(...)`, `+cmd|...`, `@SUM` cells | Saved as plain text. `/api/export/leads.csv` starts every risky cell with `'`. Rejected rows keep the original cell, and the web UI's `safeCell` adds the quote when that file is downloaded |
| Emoji and Devanagari names (`Café Mumbaiya 🍵`, `शुभ लाभ ट्रेडर्स`, `महेश अग्रवाल`) | Kept exactly as written |
| Team sheet tabs via `/api/leads/import-sheet` (Leads, Demo Shared, Decision Makers) | **Was:** a BOM on the Leads tab hid `Company Name`, so 0 leads were created with no error. **Now:** works (fixed). The other team-sheet behaviour (rows grouped per company, furthest status wins, junk phones, decision makers attached as other_contacts, `(company page)`/`Notes` rows skipped) also works |

## Bugs found and fixed (smallest change each, separate commits)

1. **Team sheet / quick paste brought back do-not-contact emails** (`app/routes_enrich.py`, `app/main.py`). Both older
   importers only checked erased hashes, not the `suppressed` list, so a bounced or unsubscribed address could come back
   as a fresh `new` lead. The smart importer already skips these rows. Severity: high (breaks PRD §4 "a no is never a yes").
2. **A BOM in the team sheet CSV gave 0 leads, silently** (`app/sheet_import.py`). Severity: medium.
3. **Excel serial dates and date-times gave job age 0** (`app/places.py`). Severity: medium (hurts scoring).
4. **IndiaMART Lead Manager columns** (`app/header_map.py`). `SENDER_COMPANY` was not imported, so every company was lost,
   and `QUERY_PRODUCT_NAME` was read as the contact's name. Added aliases and extra-column names. In the same file, a
   Sales Navigator `Company Employee Count` alias was added, and a column known to be "extra" (such as
   `Sales Navigator Profile URL`) can no longer be turned into the website by its values. Severity: high for IndiaMART
   files.
5. **Two-row merged headers, a clearer UTF-16 message, and the team sheet's Lead Status kept as a note**
   (`app/lead_import.py`). When a team-sheet row merged into an existing lead, its "Lead Status" (e.g. `Demo Scheduled`)
   vanished. It is now kept as a timeline note. The stage is still never changed by an import, and re-imports stay
   unchanged. Severity: medium.
6. **Hygiene missed the same person at the same company in the same city when the two websites differed**
   (`app/hygiene_report.py`). The importer is right not to merge these on its own. The report now lists them, the reason
   says the websites differ, and a person decides. Severity: medium.
7. **Missing provenance on a company-level fill** (`app/lead_merge.py`). When a directory row with no person filled a
   missing phone or email, no `field_sources` entry was written. Severity: low.

## Unified list from six sources (day 1 to day 6, using time travel on `now_iso`)

Apollo → Sales Navigator (Evaboot style) → Lusha → Naukri scrape → IndiaMART Lead Manager → team sheet (Leads tab). The
Udaan Retail HR head unsubscribes through the signed `/u/` link after day 1. Lotus Pharma's HR address was on the
suppression list from the start. Checked:

- **One lead per company.** 7 companies give 8 leads: Brightpath Edu appears twice because Apollo gave `brightpath.example`
  and Sales Navigator gave `brightpathedu.example`. Each different person goes into `other_contacts`, with its source
  and the date it was added (e.g. Neha Kulkarni from Sales Navigator on day 2).
- **No lost fields.** Every email and phone from every row of every file is on the final list, except rows on the
  do-not-contact list. Lists are combined, not replaced.
- **Conflicts recorded, not overwritten.** The first value is kept. Each different value is stored with its field,
  value, source, import id and day (e.g. Kaveri Foods title `VP People` from sales_navigator on day 2, and sector
  `Food` / `FMCG` from lusha and bmi_sheet).
- **Provenance per field.** `field_sources` names the source of each field (company and email from apollo, linkedin_url
  from sales_navigator, open_roles from job_board, contact from indiamart, ...). Every filled key field has a source.
- **Suppressed and unsubscribed rows are never brought back.** Lotus Pharma is skipped on day 3 and day 6. Udaan stays
  `unsubscribed` with consent cleared. A new person (Sneha Rao) and an address are added to it, but it stays closed.
- **Undoing the last import** (the team sheet) removes only the lead it created (Zenith Motors). Details it added to
  existing leads stay, as the undo note says. A second undo returns 400.
- **Hygiene** then reports exactly one cluster (Brightpath, "websites differ"). Merging it keeps the Apollo lead, takes
  the LinkedIn URL from Sales Navigator, and records the second domain and the title as conflicts. After that there is
  one lead per company and no duplicate clusters.
- Re-importing all six files changes nothing.

## Limitations and recommendations (not changed)

- **Several jobs from one company in one job-board file.** Only the first job's `role` and `open_roles` are kept (both
  job links are kept). In the Naukri fixture, the second Sharma Logistics posting (Delivery Executive, 40 openings) is
  dropped. The team-sheet importer adds up openings and joins roles, but the smart importer does not. Recommendation:
  for rows that are duplicates inside the same file, add `open_roles` together and join distinct `role` values.
- **UTF-16 and `.xlsx`** can't be read in the browser (`file.text()` is UTF-8 only). Recommendation: in
  `ImportWizard.jsx`, read an ArrayBuffer and pick the decoder from the BOM (`TextDecoder('utf-16le')` for `FF FE`), and
  either parse `.xlsx` with SheetJS or say clearly that it is not supported.
- **US dates** (`10/13/2026`) are not read, because `dd/mm` vs `mm/dd` can't be told apart. Indian exports use
  `dd/mm`, which is read.
- **The source label is guessed when no `source` is given.** A generic file with `Email (2)` / `Phone (2)` columns is
  detected as "Lusha export". The Excel fixture with `Contact Person` / `Remarks` is labelled `bmi_sheet`. Provenance
  is then wrong. Recommendation: show the detected source in the wizard as an editable field (the API already accepts
  `source`). Detection also needs `strong` signals that are not just numbered duplicate headers.
- **IndiaMART Lead Manager has no format of its own.** It is "Unrecognised layout", so the user must type the source.
  Its leads are *buyer enquiries* (people who asked to buy from you), which are not employer prospects. Recommendation:
  add an `indiamart_leads` format only if the team really imports these files.
- **The Decision Makers tab compares names exactly** (`known` set in `attach_people`), so `ankita verma` and
  `Ankita Verma` would both be added. It also attaches people to unsubscribed companies. That is harmless for sending,
  because they have no email, but it is worth a note on the lead.

## Formats researched (sources)

| Tool | Headers used in the fixtures | Source |
|---|---|---|
| Apollo | First/Last Name, Title, Company, Company Name for Emails, Email, Email Status, Mobile/Corporate Phone, # Employees, Person/Company Linkedin Url, Apollo Contact/Account Id | https://knowledge.apollo.io/hc/en-us/articles/4409237712141-Export-Contacts-to-a-CSV (columns can be configured; the names follow the app's existing Apollo rules) |
| Sales Navigator (Evaboot) | Full Name, Job Title, Company Name, Company Domain, Location, Sales Navigator Profile URL, Linkedin URL, Company Employee Count | https://intercom.help/evaboot/en/articles/10600662-how-does-the-sales-navigator-export-work |
| Lusha | Email 1/2, Phone 1/Phone 1 Type/Phone 2, Company Domain, LinkedIn URL | https://docs.lusha.com/user-guide/export/export-contacts-to-csv-file , https://docs.lusha.com/user-guide/integrations-basics/fields-for-field-mapping-and-descriptions |
| Naukri | No official employer export. The fixture is a typical job scrape (Company Name, Job Title, Location, Experience, Salary, Openings, Posted On, Job URL, Key Skills) | Matches the app's job-board rules (`app/header_map.py`) |
| IndiaMART Lead Manager | UNIQUE_QUERY_ID, QUERY_TIME, SENDER_NAME, SENDER_COMPANY, SENDER_MOBILE, SENDER_EMAIL, SENDER_ADDRESS/CITY/STATE/PINCODE, QUERY_PRODUCT_NAME | https://help.indiamart.com/knowledge-base/lms-crm-integration-v2 , https://help.indiamart.com/knowledge-base/integration-of-indiamarts-lead-manager-crm-push-api-with-third-party-crms-real-time-push-of-leads |
| Team Google Sheet | Leads / Demo Shared / Decision Makers headers | Existing tests and `app/sheet_import.py` |
| Excel CSV / UTF-16 | "CSV UTF-8" writes a BOM, and the list separator follows the locale (`;`). "Unicode Text" is tab-separated UTF-16LE | Python `codecs` / WHATWG Encoding (`Blob.text()` uses UTF-8 decode) |

## Final unified column mapping (source header → canonical field)

### `1_apollo.csv` — detected as "Apollo export", source label `apollo`

| Source header | Canonical field | How it was matched |
|---|---|---|
| First Name | `first_name` | known name |
| Last Name | `last_name` | known name |
| Title | `title` | known name |
| Company | `company` | known name |
| Company Name for Emails | `company` | known name |
| Email | `email` | known name |
| Email Status | (not imported) | extra column, not lead data |
| Seniority | (not imported) | extra column, not lead data |
| Departments | (not imported) | extra column, not lead data |
| Mobile Phone | `phone` | known name |
| Corporate Phone | `phone` | known name |
| # Employees | `company_size` | known name |
| Industry | `sector` | known name |
| Person Linkedin Url | `linkedin_url` | known name |
| Website | `website` | known name |
| Company Linkedin Url | `company_linkedin_url` | known name |
| City | `city` | known name |
| State | `state` | known name |
| Country | `country` | known name |
| Apollo Contact Id | (not imported) | extra column, not lead data |
| Apollo Account Id | (not imported) | extra column, not lead data |

### `2_salesnav.csv` — detected as "LinkedIn Sales Navigator export (Evaboot / Phantombuster style)", source label `sales_navigator`

| Source header | Canonical field | How it was matched |
|---|---|---|
| First Name | `first_name` | known name |
| Last Name | `last_name` | known name |
| Full Name | `contact_name` | known name |
| Job Title | `title` | known name |
| Company Name | `company` | known name |
| Company Domain | `website` | known name |
| Location | `location` | known name |
| Industry | `sector` | known name |
| Sales Navigator Profile URL | (not imported) | extra column, not lead data |
| Linkedin URL | `linkedin_url` | known name |
| Email | `email` | known name |
| Duration In Role | (not imported) | no match |
| Company Employee Count | `company_size` | known name |

### `3_lusha.csv` — detected as "Lusha export", source label `lusha`

| Source header | Canonical field | How it was matched |
|---|---|---|
| First Name | `first_name` | known name |
| Last Name | `last_name` | known name |
| Job Title | `title` | known name |
| Company Name | `company` | known name |
| Company Domain | `website` | known name |
| Email 1 | `email` | known name |
| Email 2 | `email` | known name |
| Phone 1 | `phone` | known name |
| Phone 1 Type | (not imported) | no match |
| Phone 2 | `phone` | known name |
| LinkedIn URL | `linkedin_url` | known name |
| Country | `country` | known name |
| City | `city` | known name |
| Company Size | `company_size` | known name |
| Company Industry | `sector` | known name |

### `4_naukri.csv` — detected as "Job board export (Naukri / Indeed / Shine / Apna)", source label `job_board`

| Source header | Canonical field | How it was matched |
|---|---|---|
| Company Name | `company` | known name |
| Job Title | `role` | Job board export (Naukri / Indeed / Shine / Apna) rule |
| Location | `location` | known name |
| Experience | (not imported) | extra column, not lead data |
| Salary | (not imported) | extra column, not lead data |
| Openings | `open_roles` | known name |
| Posted On | `posted_on` | known name |
| Job URL | `job_link` | known name |
| Key Skills | (not imported) | extra column, not lead data |

### `5_indiamart.csv` — detected as "Unrecognised layout", source label `indiamart (given by the user)`

| Source header | Canonical field | How it was matched |
|---|---|---|
| UNIQUE_QUERY_ID | (not imported) | extra column, not lead data |
| QUERY_TIME | (not imported) | extra column, not lead data |
| SENDER_NAME | `contact_name` | known name |
| SENDER_COMPANY | `company` | known name |
| SENDER_MOBILE | `phone` | keyword in name |
| SENDER_EMAIL | `email` | keyword in name |
| SENDER_ADDRESS | `address` | keyword in name |
| SENDER_CITY | `city` | keyword in name |
| SENDER_STATE | `state` | keyword in name |
| SENDER_PINCODE | (not imported) | extra column, not lead data |
| QUERY_PRODUCT_NAME | (not imported) | extra column, not lead data |

### `6_team_leads.csv` — detected as "Team sheet — Leads tab", source label `bmi_sheet`

| Source header | Canonical field | How it was matched |
|---|---|---|
| Company Name | `company` | known name |
| Contact Person | `contact_name` | known name |
| Designation | `title` | known name |
| Contact Number | `phone` | known name |
| Email ID | `email` | known name |
| City | `city` | known name |
| Company Sector | `sector` | known name |
| Open Position | `role` | known name |
| No. of Openings | `open_roles` | known name |
| Days Live | `job_age_days` | known name |
| Job Portal Source | `source_detail` | known name |
| Job Posting Link | `job_link` | known name |
| Lead Status | `lead_status` | known name |
| Remarks | `notes` | known name |
| Caller Name | (not imported) | extra column, not lead data |

The team sheet's Demo Shared and Decision Makers tabs go through `/api/leads/import-sheet` with fixed columns, not this
mapper. Demo Shared uses Company Name, Contact Person, Designation, Contact Number, Email ID, `Demo Given Date` →
`demo_given`, and `Status` → `demo_status` and the stage. Decision Makers uses Company, Name, `Designation (as shown)` →
title, `LinkedIn Profile URL` → linkedin_url, `Where I found it` → found_at, Confidence and `Fits 50-400?`.
