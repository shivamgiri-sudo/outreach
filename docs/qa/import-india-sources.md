# QA: importing from Indian job boards and business directories

Branch `qa/import-india-sources`. Tests: `tests/qa/test_import-india-sources.py` (end-to-end checks and checks per format) and
`tests/qa/test_import-india-sources_regressions.py` (one test per bug fixed). Fixtures are in
`tests/qa/fixtures/import-india-sources/`. They are fictional: made-up company names, `*.example` domains and
`90000 1xxxx` numbers. `make_fixtures.py` in the same folder regenerates them.

## What was tested

| Source | Fixture (rows) | Layout simulated | Detected as |
|---|---|---|---|
| Naukri (team copy-paste or saved search) | `naukri_team_copy.csv` (121, plus 1 blank row and 1 duplicate row) | Company Name, Job Title, Experience, Salary, Location, Posted On, No. of Openings, Job Link, Key Skills | Job board |
| Naukri (Apify-style scraper) | `naukri_apify.csv` (60) | jobId, title, companyName, experience, salary, location, footerPlaceholderLabel, jdURL, tagsAndSkills, vacancy | Job board |
| Indeed (Apify scraper) | `indeed_apify.csv` (60) | positionName, company, location, salary, jobType, rating, reviewsCount, url, id, postedAt, externalApplyLink, scrapedAt | Job board |
| LinkedIn Jobs scrape | `linkedin_jobs.csv` (60) | title, companyName, companyUrl, location, postedTime, publishedAt, jobUrl, applicationsCount, contractType, experienceLevel, sector | Job board |
| Shine | `shine.csv` (20, CRLF) | Job Title, Company, Location, Experience, Posted Date (`10-Oct-26`), Job URL | Job board |
| foundit (Monster India) | `foundit.csv` (20) | jobTitle, companyName, locations, experience, postedDate (`dd/mm/yyyy`), jobUrl, openings | Job board |
| Apna | `apna.csv` (20) | job_title, company_name, city, area, no_of_openings, posted_on, salary (`₹12000 - ₹18000`) | Job board |
| IndiaMART Lead Manager | `indiamart_leads.csv` (50) | UNIQUE_QUERY_ID … SENDER_NAME/MOBILE/EMAIL/COMPANY/ADDRESS/CITY/STATE/PINCODE/COUNTRY_ISO, SENDER_MOBILE_ALT, SENDER_PHONE, QUERY_PRODUCT_NAME, QUERY_MESSAGE, QUERY_MCAT_NAME, RECEIVER_MOBILE, GSTIN | Unrecognised, mapped by aliases |
| JustDial (Apify-style) | `justdial_apify.csv` (60, UTF-8 BOM, `;` separated) | name, phone, address, rating, reviews, category, city, verified, url | Google Maps / directory |
| TradeIndia seller list | `tradeindia.csv` (50) | Company Name, Contact Person, Mobile, Phone, Address, City, State, Pincode, GST No, Products, Year of Establishment, Website | Business directory |
| Outscraper Google Maps | `outscraper_gmaps.csv` (80) | name, site, subtypes, category, type, phone, full_address, street, city, postal_code, state, country, country_code, latitude, longitude, rating, reviews, place_id, google_id, cid, location_link, business_status | Google Maps |
| Apify Google Maps Scraper | `apify_gmaps.csv` (40) | title, categoryName, address, street, city, postalCode, state, countryCode, website, phone, totalScore, reviewsCount, placeId, url | Google Maps |
| D7 Lead Finder | `d7_leads.csv` (30) | Name, Phone, Email, Website, Address, City, State, Zip, Country, Category, Rating, Reviews, Facebook, Instagram | Google Maps |
| A later contact sheet for 10 of the companies | `contacts_followup.csv` (10) | Company Name, Contact Person, Designation, Email ID, Contact Number, City | Team sheet |

The fixtures include messy values: names in ALL CAPS, "Pvt. Ltd." / "PRIVATE LIMITED" / "(OPC) Pvt Ltd", phones written
as +91 / 0 / 91 / spaces / dashes, STD landlines (080, 011, 0124, 022, 020, 040, 044, 0120, 033), mixed
mobile and landline cells, "Bangalore" vs "Bengaluru", "Gurgaon" vs "Gurugram", "Delhi NCR", "Bengaluru(Sector 4)",
relative ages ("30+ days ago", "Posted 7 days ago", "Just posted"), Excel-style dates, GSTINs, ratings, place IDs,
and the same company appearing on several boards.

Areas covered:
- **Rows become company-only leads.** Every job-board row becomes a lead with a company, a role and job links. It has
  no person, email, phone or title. The stage is `new`, there is no consent, and the country is IN. A job-board page
  never becomes the company's website or domain.
- **Hiring signals.** Openings, posted date turned into job age, and job links are combined across postings and across
  re-imports done days later (time travel through `today=`).
- **City aliases.** Bengaluru/Bangalore, Gurugram/Gurgaon, "Delhi NCR" against an NCR city, and New Delhi/Delhi.
- **Phones.** Landlines and mobiles in every common Indian form. A number with no STD code is flagged, not guessed. A
  mobile is preferred over a landline within a row.
- **Addresses.** Pincode, city and state are read from JustDial, Outscraper and TradeIndia addresses.
- **GST and other noise.** GST numbers, ratings, latitude/longitude, place IDs and salary never reach a lead.
- **Merging.** Company-only Naukri rows merge with a contact sheet imported later (10 merges, no new leads; the role,
  openings and job links stay). It also works in the other order, and undo removes only the leads the import created.
- **PRD rules.** Closed leads stay closed (a closed-lead warning is shown and the stage is not changed). A suppressed
  email's row is skipped.
- **No scraping or sending.** Every fixture is previewed and committed with `scraper.fetch_page`, `scraper._get`,
  `http_util.post_json`, `urllib.request.urlopen` and `socket.create_connection` patched to fail. Nothing tried to reach
  the network, nothing was queued in `outbox` or `whatsapp_outbox`, and no lead was enriched.

## Bugs found and fixed (each one is its own commit with a regression test)

1. **Salary and ID columns were read as phone numbers, which merged different companies** (high).
   `map_columns` checked what the values looked like even for columns it already knew to ignore. Apna's
   `salary` ("₹12000 - ₹18000") gave the 10-digit "number" 1200018000. Indeed's hex `id` and IndiaMART's
   `UNIQUE_QUERY_ID` also became phone numbers. Rows that share a phone are treated as one company, so all 20 Apna
   companies collapsed into **one** lead. Fix: never check the values of a known extra column, and never treat an
   `… id` column as phones. Separately, `+000…` is no longer accepted as an international number.
   (`app/header_map.py`, `app/standardise.py`)
2. **Scraper headers were mapped to the wrong fields** (high).
   - Indeed `positionName` and Apify Maps `categoryName` became the person's *name*. The rule that treats any header
     containing "name" as a person's name took them.
   - IndiaMART `SENDER_COMPANY` was dropped.
   - `RECEIVER_MOBILE` is the seller's own number and is the same on every row. It became each buyer's phone, so
     all 50 enquiries merged into one lead.
   - `QUERY_PRODUCT_NAME` and `QUERY_MCAT_NAME` were read as the contact's name.
   - Naukri `jdURL` was read as the company website (the site was then rejected, with 60 warnings).
   - Indeed `url` and `externalApplyLink` were read as the company website.

   Fix: added aliases (`sender company`, `position name`, `category name`, `jd url`, `external apply link`,
   `posted at`) and a job-board rule that maps `URL` to the job link. The receiver and query columns and Outscraper's
   `location_link` are now marked as extras. Headers containing product, category, position or job are no longer read
   as a contact name. (`app/header_map.py`)
3. **Naukri city suffixes were kept as written** (medium). "Bengaluru(Whitefield)" and "Bangalore/Bengaluru" stayed as
   raw text, so the same company became two leads. `clean_city` now also tries the text before `(` or `/`.
   (`app/places.py`)
4. **Job age was 0 for common date formats** (medium). This affected "Posted 7 days ago" and "Active 3 days ago"
   (Indeed), date-times with a space ("2026-10-20 10:20:00", from IndiaMART and Excel), and Excel serial dates
   (46316). (`app/places.py`)
5. **Later postings never raised the hiring signals** (medium). A merge only filled empty fields, so the first row's
   job age and openings won. Example: a 3-day-old posting followed by a 30-day-old one gave a job age of 3, though
   PRD §6 scores on the oldest posting. A re-import a week later did not age the lead either. A merge now keeps the
   larger `max_job_age_days` and `open_roles`. This is idempotent: importing the same file again changes nothing.
   (`app/lead_merge.py`)
6. **"Delhi NCR" against an NCR city made two leads** (medium). Job boards say "Delhi NCR" where directories say
   Gurgaon or Noida, and the same company became two leads. A company match now accepts Delhi NCR against Delhi,
   Gurugram, Noida, Greater Noida, Ghaziabad or Faridabad. Other cities still count as separate branches.
   (`app/lead_merge.py`)
7. **A "Delhi NCR" location left the country empty** (low to medium). This lost the India fit points in scoring.
   (`app/places.py`)

## Limitations (not fixed; recommendations)

- **Naukri `footerPlaceholderLabel`** holds the posted age ("2 Days Ago") under a name the importer cannot know.
  Recommendation: the user maps it to "Job posted on" in the preview (the mapping is remembered), or add the alias
  if the team uses that actor.
- **Job age is frozen at import.** The importer stores `max_job_age_days`, not the posted date, so the age does not
  grow over time. Recommendation: also store `posted_on` and work out the age when scoring.
- **Openings are not summed across postings in one file.** The smart importer keeps the highest count (which is safe
  on re-import), while `sheet_import` adds them up per company. Only the first `role` is kept; the other openings
  survive only as `job_links`.
- **A landline stays the main phone after a merge.** When a landline-only listing is merged with a later row that has
  a mobile, the mobile is added to `phones` but `phone` stays the landline. Recommendation: promote a mobile on merge
  if WhatsApp or calling needs it. Consent is still required before any WhatsApp message.
- **An 11-digit number with no separator, such as "08041234567", is read as a mobile** (+91 80412 34567), because the
  80xx series is also used by mobiles. Written with a separator, "080-41234567" is read correctly as a landline.
  Recommendation: treat a column named "Landline" as a hint.
- **Pincode columns** (`SENDER_PINCODE`, `postal_code`, `postalCode`, `Zip`, `Pincode`) are not mapped. The pincode
  comes only from the address text, so IndiaMART addresses without a PIN lose it. Recommendation: add a `pincode`
  field and aliases.
- **IndiaMART's placeholder name.** When a buyer gives no name, IndiaMART puts a generic placeholder in `SENDER_NAME`
  (the fixture uses "Buyer"), and it is imported as the contact's name. Recommendation: treat "Buyer" as empty for
  IndiaMART files.
- **Multi-city postings** ("Bengaluru, Hyderabad") use the first city. Company matching on city then treats a Hyderabad
  listing of the same company as another branch.
- **Naukri RMS and Indeed employer "candidate" exports** list job seekers, not employers. They are out of scope (PRD
  sells to employers) and should not be imported as leads.
- **Salary-like text in a column with an unknown name** (for example "CTC") could still be read as a phone, because the
  phone parser accepts "12000 - 18000". Known names such as `salary` are now safe.

## Format sources

Fixture headers follow these public descriptions. Columns marked "(approx.)" follow the common shape of these exports
where no official header list is public.

- IndiaMART Lead Manager pull API v2 field list (SENDER_NAME, SENDER_MOBILE, SENDER_EMAIL, SENDER_COUNTRY_ISO,
  QUERY_TYPE, QUERY_TIME, QUERY_MESSAGE, and the placeholder name when a buyer gives none):
  https://help.indiamart.com/knowledge-base/lms-crm-integration-v2/. The other SENDER_/QUERY_/RECEIVER_ columns are
  (approx.), from public API client samples.
- Apify Google Maps Scraper (compass/crawler-google-places) output fields (title, categoryName, address, street, city,
  postalCode, state, countryCode, website, phone, totalScore, reviewsCount, placeId, url):
  https://apify.com/compass/crawler-google-places
- Outscraper Google Maps data dictionary (name, site, subtypes, phone, full_address, …):
  https://outscraper.com/google-maps-scraper-/ and https://outscraper.com/it/g-imprese-estrattore/
- Indeed scrapers on Apify (positionName, company, location, url, id, postedAt "2 days ago"/"Today",
  externalApplyLink): https://apify.com/misceres/indeed-scraper/changelog,
  https://apify.com/cirkit/indeed-jobs-scraper/output-schema,
  https://dev.to/crawlerbros/building-job-data-pipelines-with-indeed-jobs-scraper-4nll
- Naukri scrapers on Apify (companyName, title, location, salary, experience, createdDate, jobId, skills):
  https://apify.com/blackfalcondata/naukri-scraper and https://apify.com/jobsapi/naukri-jobs-search-scraper/changelog.
  The jdURL, footerPlaceholderLabel, tagsAndSkills and vacancy columns are (approx.), from Naukri's public job-search
  JSON.
- The LinkedIn Jobs, Shine, foundit, Apna, JustDial, TradeIndia and D7 Lead Finder layouts are (approx.), based on
  common scraper and directory exports. There is no official public header list for them, so they were written to be
  tolerant and to match what the team pastes in.
