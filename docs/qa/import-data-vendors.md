# QA: importing data-vendor and CRM exports

Area: smart import (`app/header_map.py`, `app/lead_import.py`, `app/lead_merge.py`, `app/hygiene_report.py`) for exports from
Lusha, ZoomInfo, Seamless.AI, RocketReach, Hunter.io, Snov.io, HubSpot (Clearbit-enriched), Salesforce, Zoho CRM and Pipedrive.

- Tests: `tests/qa/test_import-data-vendors.py` (34 scenario tests) and `tests/qa/test_import-data-vendors_regressions.py`
  (44 regression tests: 33 of them fail on `main` before the fixes).
- Fixtures: `tests/qa/fixtures/import-data-vendors/`, 11 CSVs and 191 data rows, made by `generate.py` (deterministic).
- Everything in the fixtures is made up: 60 people at 30 companies on `*.example` domains, gmail addresses tagged `.qa<n>`, and
  numbers in the fake 90000 / 90001 range. The same person shows up in several files with a different email alias, phone
  format, title or city, so cross-vendor dedupe and conflict recording get exercised.

## Formats researched

Most of these vendors don't publish their exact export headers: the column picker is inside the logged-in app. The headers
used here were put together from vendor documentation, the vendors' API field names and the column labels in their UIs.
They are representative, not byte-exact. Where a vendor has more than one variant (for example, Lusha full name versus
first and last name), the importer was checked against both.

How sure each link is: the Lusha, ZoomInfo, Outreach, Seamless, RocketReach, Hunter Domain Search and Pipedrive links came
back from web searches made during this QA. The Hunter API / Bulk Finder, Snov, HubSpot, Salesforce and Zoho links point
to those vendors' general documentation for export and field names, and were not fetched. Before relying on any header
row here, check it against a real export from each tool.

| Tool | File | Headers used (key ones) | Source |
|---|---|---|---|
| Lusha | `lusha_prospecting.csv` | First/Last Name, Job Title, Company Name, Company Domain, Email 1/2 (+ Type), Phone 1/2 (+ Type: Direct/Mobile), LinkedIn URL, Country, City, Company Size, Company Industry | https://docs.lusha.com/user-guide/export/export-contacts-to-csv-file ; https://docs.lusha.com/user-guide/integrations-basics/fields-for-field-mapping-and-descriptions |
| ZoomInfo | `zoominfo_contacts.csv` (BOM + CRLF) | ZoomInfo Contact ID, First/Middle/Last Name, Salutation, Job Title, Management Level, Direct Phone Number, Mobile phone, Company HQ Phone, Email Address, Email Domain, Contact Accuracy Score, LinkedIn Contact Profile URL, Person City/State, Country, ZoomInfo Company ID, Employees, Employee Range, Primary Industry, Company City/State/Country | https://docs.zoominfo.com/docs/companies-contacts ; https://support.outreach.io/hc/en-us/articles/115000251613 |
| Seamless.AI | `seamless_ai.csv` (semicolon) | Email 1, Email 1 Validation, Email 1 Total AI, Personal Email, Contact Phone 1 (+ Total AI), Company Phone 1, Contact LI Profile URL, Company LI Profile Url, Contact City/State/Country, Company Staff Count Range | https://seamless.ai/customers/education/articles/seamless-ai-your-quick-start-guide |
| RocketReach | `rocketreach.csv` | Name, Title, Company, Company Domain, Email, Email Grade, Personal Email, Work Phone, Mobile Phone, LinkedIn, Location, Country Code | https://knowledgebase.rocketreach.co/hc/en-us/articles/14116760118811 ; https://rocketreach.co/resources/september-2025-product-updates |
| Hunter (Domain Search) | `hunter_domain_search.csv` | Email address, Type, Confidence score, Number of sources, Pattern, First/Last name, Department, Position, Twitter handle, LinkedIn URL, Phone number, Verification status, Last verification date, Domain name, Organization | https://help.hunter.io/en/articles/1913848-how-to-save-and-export-leads-from-domain-search ; https://hunter.io/api-documentation (domain-search fields) |
| Hunter (bulk Email Finder) | `hunter_email_finder.csv` | First name, Last name, Domain name, Email address, Confidence score, Verification status | https://help.hunter.io (Bulk Email Finder) |
| Snov.io | `snov_prospects.csv` | First/Last/Full name, Industry, Country, Location, Position, Company name, Company URL, Email, Email status, Social URL, Phone | https://snov.io/knowledgebase (prospect list export) |
| HubSpot (with Clearbit enrichment) | `hubspot_contacts.csv` | Record ID, First/Last Name, Email, Phone Number, Mobile Phone Number, Job Title, Company Name, Website URL, City, State/Region, Country/Region, Lifecycle Stage, Lead Status, Contact owner, Create Date, Industry, Number of Employees, Unsubscribed from all email | https://knowledge.hubspot.com/import-and-export/export-records ; https://knowledge.hubspot.com/properties/hubspots-default-contact-properties |
| Salesforce (lead / contact report) | `salesforce_leads_report.csv` | First/Last Name, Title, Company / Account, Email, Phone, Mobile, City, State/Province, Country, Lead Status, Lead Source, Industry, No. of Employees, Website, Lead Owner, Create Date, Email Opt Out, Do Not Call | https://help.salesforce.com/s/articleView?id=sf.reports_export.htm ; https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_lead.htm (HasOptedOutOfEmail, DoNotCall) |
| Zoho CRM (leads) | `zoho_leads.csv` | Record Id, Lead Owner, Company, First/Last Name, Title, Email, Phone, Mobile, Website, Lead Source, Lead Status, Industry, No. of Employees, Annual Revenue, Email Opt Out, City, State, Country, Zip Code, Secondary Email, Created Time, Description | https://help.zoho.com/portal/en/kb/crm/data-administration/export-data/articles/export-data ; https://www.zoho.com/crm/developer/docs/api/v6/field-meta.html |
| Pipedrive (people) | `pipedrive_people.csv` | Person - Name, Person - Email - Work/Other, Person - Phone - Work/Mobile, Person - Organization, Person - Job title, Person - Label, Person - Owner, Organization - Address | https://support.pipedrive.com/en/article/exporting-data-from-pipedrive |

The fixtures also include messy real-world values: names in ALL CAPS, "Pvt. Ltd." / "Private Limited" / "LLP" suffixes,
phones written as `+91 90001 10001`, `090000 10002`, `91-90001-10003` and `+919000110004`, Delhi and Bengaluru landlines,
"India", "IN" and "United States", a UTF-8 BOM, CRLF line endings, a semicolon-separated file, Excel-style dates
(`03/10/2026 10:05`), a blank row, a repeated row, a Hunter "not found" row, role inboxes (`hr@`, `careers@`), personal
gmail addresses next to work ones, and the same Gmail inbox written with different dots.

## Bugs found and fixed

Each fix is small and has regression tests in `tests/qa/test_import-data-vendors_regressions.py`.

| # | Severity | Bug | Fix | Commit |
|---|---|---|---|---|
| 1 | High | **CRM opt-outs were imported as fresh, contactable leads.** A Salesforce or Zoho row with `Email Opt Out = 1/true`, or a HubSpot row with `Unsubscribed from all email = true`, became a `new` lead. If the person already existed, they stayed mailable. This breaks "a no is never a yes" and the DPDP opt-out rule. | These rows are now **skipped**, with the reason shown. On commit, their emails are added to the do-not-contact list (`suppressed`), so an existing lead with that email can't be emailed either. The lead's stage is left alone, because an import never changes a stage. `Do Not Call = 1` leaves that row's phone numbers out. | `fix: honour CRM opt-outs…` (23211a3) |
| 2 | High | **ZoomInfo "Middle Name" replaced the person's full name.** The keyword rule mapped the column to `contact_name`, and `combine_name` prefers a full name, so "Ravi K Sharma" was saved as "K". | `middle name` and `salutation` were added to the known extra columns. | 92a9799 |
| 3 | High | **ZoomInfo Contact ID was imported as a phone number.** "1200000020" reads as STD 120 (Noida) plus a number. The extra-column entry `zoominfo contact id` never matched, because the header normaliser splits camel case into `zoom info contact id`. Known extra columns were also being re-sniffed by their values. Every ZoomInfo lead got a fake landline, and leads could merge on it. | The real header spellings were added. Known extra columns are no longer re-sniffed. | 92a9799 |
| 4 | Medium | **Vendor status columns were mapped to email or phone:** `Email 1 Validation`, `Email 1 Total AI`, `Personal Email Validation`, `Email Grade`, `Email Domain`, `Email Opt Out`, `Unsubscribed from all email`, `Contact Phone 1 Total AI`. Values such as "95%" or "0/1" produced `invalid_email` / `invalid_phone` warnings, and the opt-out column was consumed as an email. | `validation`, `grade`, `ai`, `opt`, `unsubscribed`, `domain` and `marketing` were added to the status words that block the email and phone keyword rules. | 92a9799 |
| 5 | Medium | **Salesforce "Company / Account" was fuzzy-matched to company size** (0.79), so the company name was lost. | An alias for `company / account` was added. Aliases were also added for Seamless `Contact LI Profile URL`, `Company LI Profile Url`, `Company Staff Count (Range)`, Pipedrive `Organization - Address` (which had been mapped to company) and RocketReach `Country Code`. | 92a9799 |
| 6 | Medium | **Wrong source on every field.** With no source typed in, imports were labelled after the closest matching format: Seamless and RocketReach became "lusha", HubSpot became "apollo", and Salesforce and Zoho became "directory". So `source` and `field_sources` named the wrong tool, against PRD §4.5 ("the source of every field stored"). | Formats were added for Seamless.AI, RocketReach, HubSpot, Salesforce, Zoho CRM and Pipedrive. | 92a9799 |
| 7 | Low (provenance) | A vendor saying the email is invalid, undeliverable or grade F was thrown away. | It is now kept as an `email_flagged` warning in the dry run and as a timeline note on the lead ("<email> flagged by source - Email Status: Invalid"). The email itself is kept, so a person decides. | 23211a3 |

## Verified behaviour (passing tests)

- **Mapping and confidence:** every vendor file is recognised as its own format. Key columns map with a confidence between
  0.7 and 1.0 and a stated reason ("known name", "keyword in name", "values look like…"). IDs, scores, owners and type
  columns are not imported.
- **Phone variants:** direct, mobile and HQ / company phones all end up in `phones` in `+91XXXXXXXXXX` form. The primary
  `phone` is a mobile when there is one, and HQ landlines are kept as secondary numbers. No row produced
  `invalid_phone`, `junk_phone` or `foreign_phone`.
- **Country names and codes:** "India", "IN" and RocketReach "Country Code" all give `IN`. When ZoomInfo has the person in
  Chennai and the company HQ in San Jose, USA, the person's location wins (1.0 against the 0.85 company columns).
- **Conflict recording:** importing ZoomInfo after Lusha matches 20 people by **same phone**, even though their email
  aliases differ. The first value is kept (`field_sources` still says `lusha`), the new alias is added to `emails`, and
  title, email, city, state and size differences are logged in `merge_conflicts` with source, import id and time.
- **Cross-vendor dedupe:** after all 11 files, in order, there are 46 leads from 191 rows. Hunter Finder and Snov are 100%
  repeats. Gmail dot variants land on one lead. Role inboxes and a second person at the same domain attach to the company as
  `other_contacts`.
- **Hygiene report:** after the imports there are **0 duplicate clusters**, so the importer and the hygiene page agree on
  what a duplicate is. More than 50 conflicts are listed, there are 3 suppressed emails and no foreign phones. When the
  same files are saved without merging (as the quick paste or older data would), the report's clusters find them by
  "same email" and "same phone", and every Snov lead lands in a cluster.
- **Undo** of the ZoomInfo import removes only the leads it created that nobody has changed since. Leads that later vendors
  enriched are kept, each with a reason.
- **Time travel:** a lead created on day 1 and merged on day 4 keeps `created_at` from day 1, and the conflict is stamped
  day 4. All leads count as stale 95 days later and none do a week later.

## Product limitations and recommendations (not changed)

1. **Do Not Call isn't remembered.** The flagged row's phones are left out, but there is no phone suppression list. A lead
   that already exists, or a later file, still carries the number. Recommendation: a phone do-not-call list next to
   `suppressed`, checked by the dialer and WhatsApp, and exportable.
2. **An opt-out is recorded only as the email being suppressed.** The matched lead's stage stays as it was, by design.
   Recommendation: show "opted out in source CRM" on the lead page and write a consent-ledger "withdrawn" entry.
3. **CRM lead statuses aren't mapped.** Salesforce "Closed - Not Converted", HubSpot "Unqualified", Zoho "Junk Lead" and
   similar all import as `new`, because `STATUS_STAGE` only knows the team sheet's words. Recommendation: map the closed or
   lost statuses to `disqualified` on create only, in keeping with "closed leads stay closed".
4. **Vendor email verdicts aren't stored on the lead** as a field (`email_status`); there is only a warning and a timeline
   note. Recommendation: store `email_status` and the vendor, let the sender skip `invalid` / grade F, and add an option
   "leave out emails the source marked invalid", like the existing role-address and free-mail options.
5. **Direct and mobile can't be told apart for Indian numbers.** Lusha `Phone 1 Type = Direct` and a mobile are both
   10-digit mobiles, so `phone_type` is "mobile" for both. The `* Type` columns are ignored. Recommendation: read
   `Phone N Type` when present.
6. **The quick paste importer** (`POST /api/leads/import`) doesn't read opt-out or Do Not Call columns. Only the import
   wizard does. Recommendation: point quick paste users to the wizard for CRM exports, or share `_source_flags`.
7. **Exact headers can drift.** Vendors let users pick columns, so headers vary. Unknown columns fall back to keyword, fuzzy
   and value sniffing, and the saved per-layout mapping (`settings.import_mappings`) covers repeat files.
