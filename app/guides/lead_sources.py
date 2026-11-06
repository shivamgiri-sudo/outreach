"""Guides: the lead-discovery and email-verification sources (app/sources), one written entry per source.

Each entry is written by hand on purpose: a new source without an entry here fails tests/test_guides.py.
"""
from .common import (CHECK_PRICES, ERR_CREDITS, ERR_KEY_REJECTED, ERR_MASKED, KEY_SAFETY, NO_COST, TEST_SOURCE, guide, hint)

CAT = "Lead data"
VERIFY = "Email verification"
SLUG_HELP = hint("One company per line. Optional: add | and the website, e.g. acme|acme.in")
SLUG_ERR = [["The test finds no jobs", "Check the identifier exactly as it appears in the careers page address. Some "
             "companies have no open jobs right now."],
            ["Contacts are not found for a company", "Add | and the company's website after its identifier, e.g. acme|acme.in."]]
CONTACT_HELP = hint("Optional. Your work email, so the service can contact you if something goes wrong.",
                    r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", "Type a full email address, or leave it empty.")

# id: (title, address example of a careers page)
BOARDS = {
    "greenhouse": ("Greenhouse job boards", "boards.greenhouse.io/acme"),
    "lever": ("Lever job postings", "jobs.lever.co/acme (EU accounts: write eu-acme)"),
    "workable": ("Workable job boards", "apply.workable.com/acme"),
    "ashby": ("Ashby job boards", "jobs.ashbyhq.com/acme"),
    "smartrecruiters": ("SmartRecruiters job boards", "jobs.smartrecruiters.com/Acme"),
    "recruitee": ("Recruitee careers sites", "acme.recruitee.com (only the part before .recruitee.com)"),
    "zoho_recruit": ("Zoho Recruit careers pages", "acme.zohorecruit.in (only the part before .zohorecruit.in)"),
}
# id: (title, summary, signup + where the key is, field hints {key: hint})
KEYED = {
    "adzuna": ("Adzuna (India jobs)", "Finds companies that are hiring in India from Adzuna's job ads.",
               "developer.adzuna.com > Register > your dashboard shows the Application ID and Application Key",
               {"app_id": hint("About 8 letters and numbers.", r"^[0-9a-fA-F]{6,12}$", "The Application ID is short (about 8 characters)."),
                "app_key": hint("About 32 letters and numbers.", r"^[0-9a-fA-F]{20,40}$", "The Application Key is about 32 characters.")}),
    "jooble": ("Jooble (job search API)", "Finds companies that are hiring from Jooble's job listings.",
               "jooble.org/api/about > fill in the access form > the key arrives by email", {}),
    "themuse": ("The Muse", "Job listings from The Muse (few Indian companies). Works without a key.",
                "Optional: themuse.com/developers > register an app > copy the key (only for a higher limit)", {}),
    "google_cse": ("Google Programmable Search", "Searches the web for company pages. Only for existing users of this Google product.",
                   "programmablesearchengine.google.com > your search engine > copy the Search engine ID; then Google Cloud "
                   "console > APIs & Services > Library > enable Custom Search API > Credentials > Create credentials > API key",
                   {"api_key": hint("Starts with AIza and is 39 characters.", r"^AIza[0-9A-Za-z_\-]{35}$",
                                    "Google API keys start with AIza. Copy it from Google Cloud console > Credentials."),
                    "cx": hint("The Search engine ID, a short mix of letters and numbers.")}),
    "brave_search": ("Brave Search API", "Searches the web for company pages.",
                     "api-dashboard.search.brave.com > subscribe to a Search plan > API Keys > Add API key", {}),
    "opencorporates": ("OpenCorporates (company registry)", "Looks up registered companies.",
                       "opencorporates.com > create an account > API page > request an API token", {}),
    "data_gov_in": ("data.gov.in company master data", "India's government open data on registered companies.",
                    "data.gov.in > Sign up > My Account > generate API key. Then open the dataset (search 'Company Master "
                    "Data'), API tab, and copy its resource id",
                    {"resource_id": hint("Looks like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                                         r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                                         "The resource id has dashes like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f (from the dataset's API tab).")}),
    "snov": ("Snov.io", "Finds work emails for people at a company.",
             "snov.io > Account settings > API > copy the User ID (client id) and the API secret", {}),
    "prospeo": ("Prospeo", "Finds work emails for people at a company.", "prospeo.io > your dashboard > API section", {}),
    "dropcontact": ("Dropcontact", "Finds and checks work emails (European service, GDPR-focused).",
                    "dropcontact.com > API settings page > copy the access token", {}),
    "lusha": ("Lusha", "Finds work emails and phones. API access depends on your plan.",
              "Ask Lusha to enable API access, then Settings > API > copy the key", {}),
    "rocketreach": ("RocketReach", "Finds work emails and phones. Needs a plan that includes the API.",
                    "rocketreach.co > Account > API > generate a key", {}),
    "peopledatalabs": ("People Data Labs", "Finds people and their work details.",
                       "peopledatalabs.com > create an account > the dashboard shows your API key", {}),
    "kickbox": ("Kickbox", "Checks an email address before you send to it.",
                "kickbox.com > API Settings > create an API key (keys starting with test_ are a free sandbox that does not "
                "really check)", {"api_key": hint("Starts with live_ (real checks) or test_ (sandbox).", "", "",
                                                   [(r"^test_", "This is a sandbox key: results are fake. Use a live_ key for real checks.")])}),
    "millionverifier": ("MillionVerifier", "Low-cost email checking before you send.", "millionverifier.com > API > copy your key", {}),
    "bouncer": ("Bouncer", "Checks an email address before you send to it.", "usebouncer.com > API > create a key", {}),
    "emailable": ("Emailable", "Checks an email address before you send to it.", "emailable.com > API > copy a key",
                  {"api_key": hint("Starts with live_ or test_.", "", "", [(r"^test_", "This is a test key: results are not real.")])}),
    "abstract": ("Abstract Email Reputation", "Checks an email address and its reputation.",
                 "abstractapi.com > add the Email Reputation API > copy the key shown for it", {}),
    "reoon": ("Reoon", "Checks an email address before you send to it.", "reoon.com > API > copy your key", {}),
}
VERIFIERS = {"kickbox", "millionverifier", "bouncer", "emailable", "abstract", "reoon"}
KEYLESS = {
    "osm_overpass": ("OpenStreetMap (Overpass)", "Finds businesses by type and city from OpenStreetMap. Free, no key."),
    "osm_nominatim": ("OpenStreetMap (Nominatim)", "Looks up places and addresses on OpenStreetMap. Free, no key."),
    "wikidata": ("Wikidata (notable Indian companies)", "Lists well-known companies from Wikidata. Free, no key."),
}


def _board(sid, title, example):
    return guide(sid, title, CAT, "Finds companies that are hiring from their public careers page. Free, no account or key.",
                 [f"Open the company's careers page. Its address looks like {example}.",
                  "The identifier is the company part of that address (for example acme).",
                  f"In this app: Integrations > Lead data > {title} > Set up. Paste one identifier per line. If you know "
                  "the company's website add it after a | sign, like acme|acme.in, so contacts can be found too. Save.",
                  "Press Run test, then use it in a campaign or on the Data sources page."],
                 cost=NO_COST, time="5 minutes", paste=[("Company identifiers", "One per line, e.g. acme or acme|acme.in")],
                 test=TEST_SOURCE, errors=SLUG_ERR, safety="Only public job pages are read. No login, nothing secret.",
                 attach=[f"hub:lead_data:{sid}", f"integration:{sid}"], fields={"slugs": SLUG_HELP},
                 keywords=f"{sid} jobs careers ats hiring slug identifier")


def _keyed(sid, title, summary, where, fields):
    verify = sid in VERIFIERS
    steps = [f"Get your key: {where}. Use the provider's own Copy button.",
             f"In this app: Integrations > Lead data > {title} > Set up. Paste it and press Save and continue.",
             "Press Run test. A green tick means the key works."]
    if verify:
        steps.append("Verifiers check each address before sending, which protects your domain from bounces.")
    return guide(sid, title, VERIFY if verify else CAT, summary, steps, cost=CHECK_PRICES + " Most have a small free allowance.",
                 time="5 minutes", paste=[("Key boxes on the card", where)], test=TEST_SOURCE,
                 errors=[ERR_KEY_REJECTED, ERR_CREDITS, ERR_MASKED], safety=KEY_SAFETY,
                 attach=[f"hub:lead_data:{sid}", f"integration:{sid}"], fields=fields,
                 related=["email_verifier"] if verify else [], keywords=f"{sid} api key {'verify' if verify else 'enrich'}")


def _keyless(sid, title, summary):
    return guide(sid, title, CAT, summary,
                 ["Nothing is required: it works as soon as you use it in a campaign.",
                  f"Optional: Integrations > Lead data > {title} > Set up, add your work email so the service can reach you "
                  "if your use causes trouble, and Save.", "Results without a website are skipped."],
                 cost="Free, run by volunteers: please keep searches small.", time="1 minute",
                 paste=[("Your contact email", "Optional: your work email")], test=TEST_SOURCE,
                 errors=[["'Too many requests'", "The free service limits use. Wait a few minutes and search a smaller area."]],
                 safety="Your contact email is sent to the service with each request; use a work address.",
                 attach=[f"hub:lead_data:{sid}", f"integration:{sid}"], fields={"contact": CONTACT_HELP},
                 keywords=f"{sid} openstreetmap free")


SMTP_PROBE = guide(
    "smtp_probe", "Mailbox probe (self-hosted)", VERIFY,
    "Asks a company's mail server whether an address exists, without sending an email. Technical: for your IT person.",
    ["Your server must be allowed to make outbound connections on port 25 (many cloud providers block it).",
     "It needs a hostname with reverse DNS (ask your hosting provider to set the PTR record), e.g. mail.yourdomain.com.",
     "Pick a sender address at a domain you own, e.g. verify@yourdomain.com.",
     "In this app: Integrations > Lead data > Mailbox probe > Set up. Paste both and Save. Press Run test."],
    cost=NO_COST, time="30 minutes for an IT person", paste=[("Server hostname", "e.g. mail.yourdomain.com"),
                                                          ("Sender address", "e.g. verify@yourdomain.com")],
    test=TEST_SOURCE, errors=[["Every result says 'not confirmed'", "Google Workspace and Microsoft 365 accept every address, "
                               "so they cannot be checked this way. Use a paid verifier for those."],
                              ["Connection timed out", "Port 25 is blocked by your hosting provider."]],
    safety="Never use this on large lists; mail servers may block your server's address.",
    attach=["hub:lead_data:smtp_probe", "integration:smtp_probe"],
    fields={"helo": hint("A hostname like mail.yourdomain.com", r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", "Type a hostname such as mail.yourdomain.com."),
            "mail_from": hint("An email at your own domain.", r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", "Type a full email address.")},
    keywords="smtp probe verify port 25")

GUIDES = ([_board(k, *v) for k, v in BOARDS.items()] + [_keyed(k, *v) for k, v in KEYED.items()]
          + [_keyless(k, *v) for k, v in KEYLESS.items()] + [SMTP_PROBE])
