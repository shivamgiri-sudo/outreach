# Lead sources (app/sources)

Every source here is lawful by construction: an official API used with the owner's own key, a public open-data endpoint, a public job-board JSON feed
that a company publishes for programmatic use, or the company's own website read through the polite crawler (robots.txt respected, SSRF guarded).
Nothing scrapes LinkedIn, Naukri, Indeed, Glassdoor, JustDial or IndiaMART, and nothing bypasses anti-bot measures (PRD §4 and §6).
Every returned value carries its source id and link (provenance), is deduplicated against existing leads and the suppression list, and is never
added again once a lead is unsubscribed, bounced or disqualified.

**How to read the status column.** "Coded from docs" means the request and response shapes were written from the provider's published
documentation and tested only against fake responses. None of the third-party sources below has been run against the live service. Try each
once with a small sample before relying on it. Where the docs could not be read in full, the note says so.

## Source table

| Source (id) | What it gives | Key needed | Free tier / cost note | Terms / ToS note | Status |
|---|---|---|---|---|---|
| Greenhouse (`greenhouse`) | Open roles per company (hiring signal) | None; company board token | Free | Public Job Board API for career sites; low volume ([docs](https://docs.greenhouse.io/job-board.html)) | Coded from docs (endpoint and fields confirmed in docs) |
| Lever (`lever`) | Open roles per company | None; site name (`eu-` prefix for EU) | Free | Public Postings API ([docs](https://github.com/lever/postings-api)) | Coded from docs (endpoint, `skip`/`limit`, `mode=json` confirmed) |
| Workable (`workable`) | Open roles per company | None; account name | Free | Uses Workable's public widget feed, not the token API ([docs](https://workable.readme.io/)) | Coded from docs and public examples; endpoint not re-checked |
| Ashby (`ashby`) | Open roles per company | None; job board name | Free | Public Job Posting API ([docs](https://developers.ashbyhq.com/docs/public-job-posting-api)) | Coded from docs (endpoint and fields confirmed) |
| SmartRecruiters (`smartrecruiters`) | Open public roles (India) per company | None; company identifier | Free | Public postings only, never internal ([docs](https://developers.smartrecruiters.com/docs/posting-api)) | Coded from docs; the docs page fetched did not list the endpoint, shape is from public examples |
| Recruitee (`recruitee`) | Open roles per company | None; account subdomain | Free | Reads the public careers-site offers feed; the token API is not used ([docs](https://docs.recruitee.com/reference/offers-get)) | Coded from public knowledge of the careers feed; not confirmed in docs |
| Zoho Recruit (`zoho_recruit`) | Open roles per company | None; account subdomain | Free | No public JSON API exists, so it reads the careers page JobPosting data via the polite fetcher | Coded; page format not verified live |
| Adzuna (`adzuna`) | Employers with live India job ads | App id + key | Developer plan with limits (see site) | Keep Adzuna attribution/link; only employer, title, city, date kept ([docs](https://developer.adzuna.com/docs/search)) | Coded from docs; India country code `in` not confirmed on the docs page |
| Jooble (`jooble`) | Employers with live job ads | API key (free on request) | Limits set by Jooble | [Terms](https://jooble.org/info/terms) ([docs](https://jooble.org/api/about)) | Coded from Jooble's sample code; the docs page only shows the access form |
| The Muse (`themuse`) | Job listings | Optional key | Free; 500 req/h without a key, 3,600 with ([docs](https://www.themuse.com/developers/api/v2)) | [API terms](https://www.themuse.com/developers/api/v2/terms) | Coded from docs. **Coverage honestly poor for India**: listings are US cities and "Flexible / Remote" (checked on a sample page); non-India listings are dropped, so it usually returns nothing |
| Google Programmable Search (`google_cse`) | Company sites and careers pages from search | API key + engine id | 100 free queries/day for existing users | Google terms. **Closed to new customers; shuts down 1 Jan 2027** ([overview](https://developers.google.com/custom-search/v1/overview)) | Coded from docs; only useful for accounts that already have it |
| Brave Search API (`brave_search`) | Company sites and careers pages from search | API key | Check the Brave dashboard; plans change ([docs](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)) | Check your plan allows storing results | Coded from docs |
| OpenStreetMap Overpass (`osm_overpass`) | Businesses with websites by category and city | None | Free; stay well under the public server's limits | ODbL, credit OpenStreetMap contributors; honest User-Agent ([policy](https://wiki.openstreetmap.org/wiki/Overpass_API)) | Coded from docs; Indian coverage patchy |
| OpenStreetMap Nominatim (`osm_nominatim`) | Named places with website/phone | None | Free, max 1 req/s, at most 4 searches per run | [Usage policy](https://operations.osmfoundation.org/policies/nominatim/): no bulk use | Coded from docs |
| Google Places (New) (`google_places`) | Businesses by type and city | API key | Google's monthly free allowance; see pricing | Google Maps Platform terms ([docs](https://developers.google.com/maps/documentation/places/web-service/text-search)) | Existing adapter, now in the registry |
| OpenCorporates (`opencorporates`) | Registered Indian companies (name, CIN, address); no contacts | API token | Free for public-benefit/open-data use only | Commercial outreach needs a paid licence ([docs](https://api.opencorporates.com/documentation/API-Reference)) | Coded from docs |
| data.gov.in (`data_gov_in`) | Registered companies from an MCA open dataset (name, status, address); no contacts | data.gov.in key + dataset resource id | Free | Government Open Data Licence - India (GODL) ([docs](https://data.gov.in/apis)) | Coded; third-party descriptions of the RoC-wise Company Master Data resource were used, the official resource page was not read |
| Wikidata SPARQL (`wikidata`) | Notable Indian companies with website, size, industry | None | Free | CC0; query limits; descriptive User-Agent ([policy](https://meta.wikimedia.org/wiki/User-Agent_policy)) | Coded from docs |
| Company website deep read (`website_deep`) | sitemap.xml pages, JSON-LD Organization/ContactPoint/Person, mailto:/tel:, the company's LinkedIn page link (text only) | None | Free, max 8 pages per company | robots.txt respected, same host only, LinkedIn never fetched ([sitemaps](https://www.sitemaps.org/protocol.html)) | Built in; tested with fake pages |
| Apollo (`apollo`) | People by domain + title; email match | Apollo key | Plan dependent; search uses no credit, a reveal about 1 | [docs](https://docs.apollo.io/) | Existing adapter; domain filter coded from docs |
| Hunter (`hunter`) | Domain search, email finder, verifier | Hunter key | Small free plan; key check is free | [docs](https://hunter.io/api-documentation/v2) | Existing + completed; coded from docs |
| Snov.io (`snov`) | Email finder, verifier (async) | Client id + secret | Trial allowance; token check free | [docs](https://snov.io/api) | Coded from docs (start/result endpoints and fields confirmed); balance endpoint not read |
| Prospeo (`prospeo`) | Verified work email for a named person | API key | Free allowance on their pricing page | [docs](https://prospeo.io/api-docs) | Coded; docs overview only, exact fields from public examples |
| Dropcontact (`dropcontact`) | Email finder (async, ~30 s) | Access token | Trial | [docs](https://developer.dropcontact.com/) | Coded from docs; no free key check |
| Lusha (`lusha`) | Work email and office phone | API key (paid plan) | No free API tier published | [docs](https://docs.lusha.com/); work contacts only | Coded from docs; field names unconfirmed |
| RocketReach (`rocketreach`) | Professional email | API key (paid plan) | None published | [docs](https://rocketreach.co/api) | Coded from docs |
| People Data Labs (`peopledatalabs`) | Work email (likelihood scored) | API key | Small free tier | [docs](https://docs.peopledatalabs.com/docs/person-enrichment-api); holds personal data, DPDP care | Coded from docs; no free key check |
| Built-in check (`builtin_verifier`) | Syntax, role and disposable lists, MX lookup over DNS-over-HTTPS | None | Free | Sends only the domain name to Cloudflare's public resolver ([docs](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)) | Built in; cannot confirm a mailbox exists |
| ZeroBounce, NeverBounce (`zerobounce`, `neverbounce`) | Mailbox verification | Key in the Email verifier card | Small trials | [ZeroBounce](https://www.zerobounce.net/docs/), [NeverBounce](https://developers.neverbounce.com/) | Existing calls (fake-tested) + free key checks coded from docs |
| Kickbox (`kickbox`) | Mailbox verification | API key (`test_` = free sandbox) | Trial | [docs](https://docs.kickbox.com/) | Coded; no free key check on a live key |
| MillionVerifier (`millionverifier`) | Mailbox verification | API key | Trial | [docs](https://developer.millionverifier.com/) | Coded; credit check free |
| Bouncer (`bouncer`) | Mailbox verification | API key | Free credits on signup | [docs](https://docs.usebouncer.com/) | Coded; credit check free |

Prices are deliberately not quoted: every provider changes them. Each card shows a credit estimate (cost per call) and the provider's own pricing page link.

## How a run uses them
- **Registry** (`app/sources/registry.py`): every source with capabilities (`discover_companies`, `discover_people`, `find_email`, `verify_email`, `hiring_signal`), connected status, cost per call and a rate limit.
- **Planner**: `plan(goal, available)` returns the sources able to do a goal, free first, then the owner's keys by credit cost.
- **Waterfall** (`waterfall.py`): per lead, finders run cheapest first; each candidate email goes through the chosen verifiers (built-in first); it stops at the first verified email, otherwise keeps the best unverified one (marked unverified). Invalid addresses are dropped; suppressed or already-owned addresses are never written.
- **Isolation**: each source runs in its own guard. A rejected key, a 429, a network error or a malformed reply is reported once and the others continue; a rejected key or rate limit switches that source off for the rest of the run.
- **Cache**: answers (including "nothing found") are kept 7 days in the `source_cache` collection (newest 800 only), so the same question is not paid for twice.
- **Credit cap**: `rules.credit_cap` (default 100) limits paid credits per run; free sources are not counted.
- **SSRF**: company identifiers can only contain letters, digits, `.`, `_`, `-` and are placed in a fixed host or path; domains are bare public host names (no IPs, `localhost`, `.local`, ports, paths). All traffic goes through the guarded HTTP helper (public addresses only, pinned IP, no redirects for API calls).
- **API**: `GET /api/sources` (catalogue, status, steps, terms), `POST /api/sources/{id}/test` (free check where the API allows it; Kickbox, Dropcontact and People Data Labs have none and say so). The Integrations > Lead data tab and the campaign wizard are built from this catalogue.

## Not included and why
| Source | Why not | Lawful alternative |
|---|---|---|
| LinkedIn (search, profiles, Sales Navigator automation) | Terms forbid scraping and automation | Export from Sales Navigator by hand and import the CSV; the website read only records a company's LinkedIn page link as text |
| Naukri, Indeed, Glassdoor, Foundit, JustDial, IndiaMART, Sulekha, AmbitionBox | Terms forbid scraping; no public API for this use | Adzuna/Jooble APIs, company careers pages and ATS feeds above |
| Clearbit / HubSpot Breeze Intelligence | The standalone Clearbit API is being sunset and Breeze has no standalone API (HubSpot community, third-party reports; not confirmed on a HubSpot docs page) | Apollo, Hunter, Snov.io, Prospeo, PDL |
| Kaspr | Its data comes from LinkedIn profiles and its API takes a LinkedIn profile as input; API access is a paid plan | Prospeo, Lusha, RocketReach with the owner's own contract |
| Startup India / DPIIT recognised startups list | The portal offers a search page but no documented bulk download or API; scraping it is not allowed | If you obtain a file lawfully (or a data.gov.in dataset), import it under Leads > Import |
| MCA21 portal directly | Paid / captcha-gated services | data.gov.in company master data (included) |
| Google Search results pages | Scraping is forbidden | Brave Search API (and Google CSE for existing users) |
| Crunchbase, ZoomInfo, Cognism, Seamless.ai | Enterprise contracts | Apollo, Hunter and the others above |
| Email-guessing by SMTP probing from this server | Gets the sending IP blacklisted and is intrusive | Paid verifiers; the built-in MX check |
