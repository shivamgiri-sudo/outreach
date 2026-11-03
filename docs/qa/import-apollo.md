# QA: importing Apollo.io exports

Branch `qa/import-apollo`. Tests: `tests/qa/test_import-apollo.py` (21 end-to-end tests) and
`tests/qa/test_import-apollo_fixes.py` (13 regression tests). Fixtures: `tests/qa/fixtures/import-apollo/`.
All fixture people, companies, domains (`*.example`) and numbers (`90000 xxxxx`, `40xx xxxx`) are made up.

## Formats researched

| Format | Columns used | Source |
|---|---|---|
| Apollo people / contacts CSV export | First Name, Last Name, Title, Company Name, Company Name for Emails, Email, Email Status, Primary/Secondary/Tertiary Email (+ Source, Status, Verification Source, Catch-all, Last Verified At), Seniority, Departments, Sub Departments, Contact Owner, Account Owner, Work Direct Phone, Home Phone, Mobile Phone, Corporate Phone, Other Phone, Company Phone, Do Not Call, Stage, Lists, Last Contacted, # Employees, Industry, Keywords, Person Linkedin Url, Website, Company Linkedin Url, Facebook Url, Twitter Url, City/State/Country, Company Address/City/State/Country, Technologies, Annual Revenue, Funding columns, Apollo Contact Id, Apollo Account Id, engagement flags | https://knowledge.apollo.io/hc/en-us/articles/4409237712141-Export-Contacts-to-a-CSV (export, editable column settings, "verified emails only" choice); https://knowledge.apollo.io/hc/en-us/articles/4409161532045-Upload-a-CSV-of-Contacts-to-Apollo (field names Work Direct Phone, Corporate Phone, etc.) |
| Apollo account / company export | Company Name, # Employees, Industry, Website, Company Linkedin Url, Company Street/City/State/Country/Postal Code/Address, Company Phone, SIC/NAICS Codes, Short Description, Founded Year, Logo Url, Subsidiary of (+ Organization ID), intent topics/scores, Apollo Account Id | same Apollo help centre articles (account export uses the same "Edit export CSV settings" column picker) |
| Locked email placeholder `email_not_unlocked@domain.com` | written in place of an email the account has not unlocked | https://community.clay.com/x/support/vlvaxb5jvgk8/troubleshooting-apollo-api-enrichment-email-issues, https://apify.com/supreme_coder/apollo-scraper/issues/issue-emailnotunlock-IQD059TzdU2oZqmqs |
| Phones exported as `'+91 80 4000 1500` | leading apostrophe so Excel keeps the `+` | seen in Apollo exports; not documented officially, treated as a tolerant-input requirement |
| Edited re-export | Excel round trip: UTF-8 BOM, `;` delimiter, mixed CRLF/LF, renamed headers (Name, Designation, Organisation, Official Email, Direct No., Mobile No., Board Line, Emp Size, ...) | constructed from common Indian sales-ops sheet headers |

Apollo does not publish one canonical column list; the column set is user-configurable, so the importer must
tolerate missing and extra columns. Fixtures cover the default set as of late 2026.

## Fixtures

* `apollo_people_export.csv`: 168 rows, 40 companies, duplicate people, 17 locked emails, five phone columns plus
  Company Phone, ALL CAPS and Hinglish names, `Pvt. Ltd.` suffixes, a free-mail contact, a `gmial.com` typo,
  owner columns filled with our own mailbox, blank rows.
* `apollo_accounts_export.csv`: 59 rows (57 companies + 2 duplicates), landline-only companies without websites.
* `apollo_people_edited.csv`: 63 rows, BOM + `;` + renamed headers.

## What was verified

* Format detected as Apollo; every meaningful column mapped (names, title, company, 3 emails, 6 phone columns,
  size, industry, LinkedIn person/company, website, city/state/country, address); bookkeeping columns ignored.
* First + Last name combined and cased (`RAVI`,`KUMAR` -> Ravi Kumar); legal suffix kept in `company_legal`.
* Phones: a mobile is preferred as primary over Work Direct / Corporate landlines; all numbers kept in `phones`.
* Domain from Website, else company email (`website_guess`), never free mail; never `domain.com`, facebook,
  amazonaws logo host.
* Size buckets from `# Employees` (160 -> 51-200, 350 -> 201-500, 820 -> 501-1000).
* Seniority/department derived from the title (Apollo's own Seniority/Departments columns are ignored).
* LinkedIn URLs rebuilt as `https://www.linkedin.com/...` only; `javascript:`/`ftp:` inputs cannot get through.
* Locked emails flagged invalid and left out; a row with only a locked email is rejected.
* Dedupe within file (one lead per company, colleagues in `other_contacts`) and against existing leads;
  re-importing the same file creates nothing.
* Merge never overwrites (existing manual values and `field_sources` kept; blanks filled).
* Closed leads (unsubscribed, disqualified) stay closed; import never sets consent; stage stays `new`.
* Do-not-contact addresses skipped (smart import and, after fix 5, quick paste).
* Provenance: `source`, `import_id`, `field_sources`, timeline entry, contact `source`, import history.
* Undo removes only untouched created leads; repeated undo refused; multi-day (day 1 accounts, day 3 people)
  undo keeps leads the later import merged into.

## Bugs found and fixed

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | High | Contact Owner / Account Owner (our own mailbox) sniffed as Email: a 168-row export collapsed into one lead; Facebook/Twitter/Logo Url sniffed as Website | known extra columns are not re-mapped by value sniffing; Logo Url / Subsidiary of added to extras (`app/header_map.py`) |
| 2 | High | `email_not_unlocked@domain.com` accepted as a real address: unrelated companies merged on "same email", domain guessed as domain.com | placeholder treated as invalid; quick paste drops uncleanable emails (`app/standardise.py`, `app/lead_import.py`) |
| 3 | High | `'+91 80 4000 1500` (apostrophe prefix) parsed as a mobile: Bengaluru landlines became the lead's primary "mobile", a WhatsApp candidate | leading apostrophe stripped in `parse_phone` |
| 4 | Medium | Board line shared by two unrelated companies merged an Apollo contact into the wrong account and filled its email/domain | phone match skipped when company names clearly differ (similarity < 0.5) and domains differ (`app/lead_merge.py`) |
| 5 | Medium | Quick paste ignored the do-not-contact list and re-created leads for people who unsubscribed (smart import skips them) | quick paste counts them as `suppressed` (`app/main.py`) |
| 6 | Medium | Re-pasting the same export via quick paste created 10 duplicate leads (merge re-keyed the lead) | quick paste treats a lead's email/phone/domain keys as seen (`app/main.py`) |
| 7 | Low | "Emp Size" header (edited re-exports) ignored | alias added |

## Limitations (not fixed, recommendations)

* **Quick paste is per person, not per company.** The people export becomes 148 leads via quick paste vs 40
  company leads via the smart import; colleagues are separate leads, so one company can be approached several
  times. When a row is merged into a company lead only to fill its domain, that person's own email is dropped,
  and a second paste re-creates them (1 row in the fixture). Recommendation: route quick paste through
  `run_import` or show a banner steering Apollo files to the smart import.
* **Quick paste keys only on email, mobile or website**: 8 landline-only accounts without a website are rejected
  (the smart import keeps them). Quick paste's `normalise_phone` also treats `+91 80 ...` landlines as 10-digit
  mobiles because it does not read STD codes.
* **Shared-phone guard trade-off**: an abbreviation vs full name with no shared domain ("TCS" vs "Tata
  Consultancy Services") now makes a separate lead instead of merging on phone; the hygiene report surfaces it.
* **Apollo's Seniority / Departments / Email Status columns are ignored**; seniority comes from the title.
  Recommendation: use Email Status = "Unavailable"/"Extrapolated" as a deliverability hint.
* **LinkedIn cleaner extracts a slug from any string containing `linkedin.com/in/`** (even under another host);
  output is always a safe https linkedin.com link, so this is not a security issue.
* **Do Not Call column is ignored**; recommendation: honour `true` by skipping call tasks for that lead.
