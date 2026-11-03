"""Map any lead export's column names to Outreach OS fields, and recognise which tool the file came from.

Matching order per column: a saved mapping for this file layout, the detected format's own rules, the alias list
(case, space and punctuation do not matter), keyword rules, a difflib fuzzy match, then a look at the values
(a column full of email addresses is an email column whatever it is called). Every column gets a confidence.
"""
import hashlib
import re
from difflib import SequenceMatcher, get_close_matches

from .standardise import clean_email, clean_text, parse_phones, host_of

FIELDS = {
    "company": "Company", "contact_name": "Full name", "first_name": "First name", "last_name": "Last name",
    "title": "Person's job title", "email": "Email", "phone": "Phone", "website": "Website or domain",
    "linkedin_url": "LinkedIn (person)", "company_linkedin_url": "LinkedIn (company)", "city": "City", "state": "State",
    "country": "Country", "location": "Location (city, state, country)", "address": "Address", "sector": "Industry / sector",
    "company_size": "Company size", "open_roles": "Number of openings", "role": "Job they are hiring for",
    "job_age_days": "Days the job has been live", "posted_on": "Job posted on", "job_link": "Job link",
    "lead_status": "Lead status", "source_detail": "Where it was found", "notes": "Notes",
}
MULTI_FIELDS = frozenset({"email", "phone", "notes", "job_link"})

_A = {
    "company": ["company", "company name", "organisation", "organization", "organization name", "organisation name",
                "account name", "account", "employer", "employer name", "firm", "firm name", "business name", "business",
                "brand", "company legal name", "current company", "hiring company", "client name", "name of company",
                "name of the company", "establishment name", "shop name", "store name", "company / organisation",
                "sender company"],
    "contact_name": ["full name", "name", "contact name", "contact person", "person name", "lead name", "prospect name",
                     "decision maker", "hr name", "hr contact", "contact person name", "spoc", "spoc name", "poc",
                     "poc name", "owner name", "proprietor name", "concerned person", "key person", "representative",
                     "sender name"],
    "first_name": ["first name", "firstname", "first", "given name", "fname", "contact first name", "person first name"],
    "last_name": ["last name", "lastname", "last", "surname", "family name", "lname", "contact last name",
                  "person last name"],
    "title": ["title", "job title", "designation", "position", "role", "current title", "current position",
              "current designation", "contact title", "contact job title", "person title", "occupation",
              "designation as shown", "position title", "contact designation", "hr designation", "person job title"],
    "email": ["email", "e-mail", "email address", "email id", "e-mail id", "mail id", "mail", "work email", "business email",
              "office email", "corporate email", "email 1", "primary email", "contact email", "person email",
              "professional email", "direct email", "verified email", "official email", "hr email", "hr email id"],
    "phone": ["phone", "phone number", "phone no", "mobile", "mobile number", "mobile no", "mobile phone", "cell",
              "cell phone", "contact number", "contact no", "telephone", "tel", "landline", "landline number", "whatsapp",
              "whatsapp number", "work phone", "work direct phone", "direct phone", "direct dial", "direct phone number",
              "first phone", "phone 1", "mobile 1", "primary phone", "contact phone", "hr contact number", "hr phone",
              "phone numbers", "mobile phone number", "business phone", "personal phone", "mob", "mob no", "ph no"],
    "website": ["website", "web site", "website url", "company website", "company url", "url", "domain",
                "company domain", "domain name", "web", "site", "homepage", "home page", "company website url",
                "website address", "organization domain", "company web address"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url", "person linkedin url",
                     "profile url", "linkedin link", "contact linkedin url", "linkedin contact profile url", "li url",
                     "profile link", "linkedin profile link", "default profile url", "linkedin id"],
    "company_linkedin_url": ["company linkedin url", "company linkedin", "linkedin company url", "company linkedin page",
                             "organization linkedin", "linkedin company page", "regular company url", "company profile url"],
    "city": ["city", "town", "contact city", "person city", "city name", "office city", "hq city"],
    "state": ["state", "province", "state province", "contact state", "person state", "state name", "region"],
    "country": ["country", "country name", "contact country", "person country", "nation"],
    "location": ["location", "person location", "contact location", "geography", "job location", "based in",
                 "work location", "place"],
    "address": ["address", "full address", "company address", "street address", "business address", "office address",
                "registered address", "postal address", "street", "address line 1", "complete address", "address 1"],
    "sector": ["industry", "industries", "sector", "company sector", "vertical", "business category", "category",
               "main category", "categories", "company industry", "primary industry", "industry type", "nature of business",
               "segment", "category name"],
    "company_size": ["company size", "size", "employees", "number of employees", "employee count", "no of employees",
                     "# employees", "headcount", "staff size", "employee range", "company employees",
                     "company size employees", "team size", "employee strength", "company headcount", "employees range", "emp size",
                     "size range", "estimated num employees", "head count", "company employee count"],
    "open_roles": ["open roles", "openings", "no of openings", "number of openings", "vacancies", "vacancy",
                   "no of positions", "job openings count", "hiring count", "total openings", "openings count"],
    "role": ["open position", "open positions", "job opening", "hiring for", "vacancy title", "position open", "opening",
             "job position", "job role", "role hiring for", "requirement", "job profile", "position name"],
    "job_age_days": ["days live", "days open", "job age", "age days", "days since posted", "days active", "max job age days"],
    "posted_on": ["posted on", "date posted", "posted date", "posting date", "post date", "job posted on", "published on",
                  "published date", "posted at"],
    "job_link": ["job link", "job url", "job posting link", "job posting url", "apply link", "job page", "job listing url",
                 "posting link", "job post link", "jd url", "external apply link"],
    "lead_status": ["lead status"],
    "source_detail": ["source", "lead source", "job portal source", "where i found it", "found at", "data source",
                      "list source", "portal", "job portal", "job source", "platform", "list name"],
    "notes": ["notes", "note", "remarks", "remark", "comments", "comment", "call outcome", "feedback", "additional info",
              "other info"],
}
# Lower-priority aliases: when two columns give the same single field, the higher confidence wins.
_SECONDARY = {
    "company name for emails": ("company", 0.9), "company city": ("city", 0.85), "company state": ("state", 0.85),
    "company country": ("country", 0.85), "company location": ("location", 0.85), "company phone": ("phone", 0.9),
    "corporate phone": ("phone", 0.9), "company hq phone": ("phone", 0.85), "office phone": ("phone", 0.9),
    "hq phone": ("phone", 0.85), "home phone": ("phone", 0.85), "other phone": ("phone", 0.85), "phone 2": ("phone", 0.9),
    "phone 3": ("phone", 0.85), "mobile 2": ("phone", 0.9), "email 2": ("email", 0.9), "email 3": ("email", 0.85),
    "secondary email": ("email", 0.9), "other email": ("email", 0.85), "personal email": ("email", 0.85),
    "alternate email": ("email", 0.85), "alt email": ("email", 0.85), "additional email": ("email", 0.85),
    "company address": ("address", 0.9), "hr contact": ("contact_name", 0.9),
    "headline": ("title", 0.7),
    "company / account": ("company", 1.0), "contact li profile url": ("linkedin_url", 1.0),
    "company li profile url": ("company_linkedin_url", 1.0), "company staff count": ("company_size", 1.0),
    "company staff count range": ("company_size", 0.95), "organization - address": ("address", 0.9),
    "country code": ("country", 0.9),
}
_FILLERS = frozenset({"no", "of", "number", "num", "nos", "the", "a", "s"})
# Columns every export carries that are not lead data; named here so fuzzy matching never grabs them.
KNOWN_EXTRA = (
    "query product name", "query message", "query time", "query type", "unique query id", "sender pincode",
    "number of sources", "confidence score", "email status", "email confidence", "vmid", "query", "timestamp",
    "apollo contact id", "apollo account id", "zoominfo contact id", "zoominfo company id", "record id", "id", "sr no",
    "serial no", "sl no", "index", "caller name", "contact owner", "account owner", "lists", "stage", "last contacted",
    "email sent", "email open", "email bounced", "replied", "demoed", "rating", "reviews", "reviews count", "latitude",
    "longitude", "plus code", "place id", "cid", "gst no", "gstin", "gst", "experience", "salary", "skills", "key skills",
    "job description", "twitter handle", "twitter url", "facebook url", "seniority", "department", "departments",
    "management level", "keywords", "technologies", "annual revenue", "total funding", "latest funding",
    "position level", "next follow up date", "demo date and time", "contact confidence", "sales navigator url",
    "sales navigator profile url", "job title hierarchy level", "job function", "fits 50 400", "opening hours",
    # LinkedIn tools: previous jobs, middle names and Sales Navigator-only links are not the current lead
    "past experience company name", "past experience company url", "past experience company title", "company2",
    "company url2", "job title2", "job location2", "middle name", "sales profile", "recruiter profile", "school url",
    "profile image url", "search account profile name", "search account profile id", "last state",
    "salutation", "ZoomInfo Contact ID", "ZoomInfo Company ID", "ZoomInfo Contact Profile URL",
    "logo url", "subsidiary of", "subsidiary of organization id",
    "receiver mobile", "receiver name", "receiver email", "query product name", "query mcat name", "location link",
)


def norm_header(header) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", clean_text(header).lstrip("﻿"))
    text = text.lower().replace("&", " and ").replace("#", " number ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"\blinked in\b", "linkedin", re.sub(r"\be mail\b", "email", text))
    return re.sub(r"\s+", " ", text)


def loose_header(header) -> str:
    return " ".join(t for t in norm_header(header).split() if t not in _FILLERS)


ALIAS = {}
EXACT = {norm_header(n) for names in _A.values() for n in names} | {norm_header(n) for n in _SECONDARY}
EXTRA = {loose_header(n) for n in KNOWN_EXTRA}
for _field, _names in _A.items():
    for _name in _names:
        ALIAS.setdefault(loose_header(_name), (_field, 1.0))
for _name, _value in _SECONDARY.items():
    ALIAS[loose_header(_name)] = _value

# keyword rules: (words that must appear, words that must not, field)
_STATUS_WORDS = {"status", "verified", "verification", "type", "confidence", "score", "count", "date", "owner", "quality",
                 "sent", "open", "opened", "bounced", "catch", "valid", "source", "code", "validation", "grade", "ai",
                 "opt", "unsubscribed", "domain", "marketing"}
KEYWORD_RULES = (
    ({"linkedin", "company"}, set(), "company_linkedin_url"), ({"linkedin"}, set(), "linkedin_url"),
    ({"email"}, _STATUS_WORDS, "email"), ({"mail"}, _STATUS_WORDS, "email"),
    ({"mobile"}, _STATUS_WORDS, "phone"), ({"phone"}, _STATUS_WORDS, "phone"), ({"whatsapp"}, _STATUS_WORDS, "phone"),
    ({"contact", "number"}, _STATUS_WORDS, "phone"), ({"website"}, set(), "website"), ({"domain"}, {"expertise"}, "website"),
    ({"first", "name"}, set(), "first_name"), ({"last", "name"}, set(), "last_name"),
    ({"company", "name"}, set(), "company"), ({"organisation"}, set(), "company"), ({"organization"}, set(), "company"),
    ({"firm"}, set(), "company"), ({"employer"}, set(), "company"),
    ({"employees"}, set(), "company_size"), ({"headcount"}, set(), "company_size"),
    ({"designation"}, set(), "title"), ({"job", "title"}, set(), "title"), ({"city"}, set(), "city"),
    ({"state"}, set(), "state"), ({"country"}, {"code"}, "country"), ({"industry"}, set(), "sector"),
    ({"sector"}, set(), "sector"), ({"openings"}, set(), "open_roles"), ({"vacancies"}, set(), "open_roles"),
    ({"address"}, {"email", "ip"}, "address"), ({"remarks"}, set(), "notes"), ({"notes"}, set(), "notes"),
    ({"name"}, {"file", "list", "campaign", "user", "caller", "owner", "sheet", "product", "category", "position", "job"},
     "contact_name"),
)


def _fmt(key, name, source, strong, weak=(), overrides=None):
    return {"key": key, "name": name, "source": source, "strong": {loose_header(h) for h in strong},
            "weak": {loose_header(h) for h in weak}, "overrides": {loose_header(k): v for k, v in (overrides or {}).items()}}


FORMATS = (
    _fmt("apollo", "Apollo export", "apollo",
         ["Company Name for Emails", "Person Linkedin Url", "Work Direct Phone", "Corporate Phone", "Apollo Contact Id",
          "Apollo Account Id", "Email Status", "Company Linkedin Url", "Seniority", "Departments", "Contact Owner"],
         ["First Name", "Last Name", "Title", "Company", "Email", "Mobile Phone", "# Employees", "Industry", "City",
          "State", "Country", "Website"]),
    _fmt("team_leads", "Team sheet — Leads tab", "bmi_sheet",
         ["Contact Number", "Email ID", "Open Position", "Company Sector", "Job Portal Source", "Job Posting Link",
          "Posting Date", "Days Live", "No. of Openings", "Position Level", "Caller Name", "Call Outcome",
          "Demo Date & Time", "Lead Status", "Next Follow-up Date", "Contact Confidence", "Company Size (employees)"],
         ["Company Name", "Contact Person", "Designation", "City", "Remarks", "LinkedIn Profile URL", "Status"],
         {"Status": "notes"}),
    _fmt("team_demo", "Team sheet — Demo Shared tab", "bmi_sheet:demo", ["Demo Given Date"],
         ["Company Name", "Contact Person", "Designation", "Contact Number", "Email ID", "Status"],
         {"Status": "notes", "Demo Given Date": "notes"}),
    _fmt("team_decision_makers", "Team sheet — Decision Makers tab", "bmi_sheet:decision_makers",
         ["Where I found it", "Designation (as shown)", "Fits 50-400?", "Confidence"],
         ["Company", "Name", "LinkedIn Profile URL"], {"Confidence": "notes"}),
    _fmt("sales_navigator", "LinkedIn Sales Navigator export (Evaboot / Phantombuster style)", "sales_navigator",
         ["Sales Navigator URL", "Sales Navigator Profile URL", "vmid", "Full Name", "Duration In Role",
          "Duration In Company", "Title Description", "Regular Company Url", "Company Location", "Default Profile Url",
          "Profile Url", "Linkedin Url", "query", "timestamp"],
         ["First Name", "Last Name", "Job Title", "Title", "Company Name", "Company", "Location", "Industry", "Email",
          "Company Url"]),
    _fmt("lusha", "Lusha export", "lusha",
         ["Phone 1", "Phone 2", "Email 1", "Email 2", "Company Domain", "Direct Phone", "Lusha"],
         ["First Name", "Last Name", "Job Title", "Company Name", "LinkedIn URL", "Country", "City", "Company Size",
          "Company Industry", "Mobile Phone"]),
    _fmt("kaspr", "Kaspr export", "kaspr", ["Work email", "Direct email", "Kaspr"],
         ["First name", "Last name", "Title", "Company", "LinkedIn", "Phone 1", "Phone 2", "Location"]),
    _fmt("dux_soup", "Dux-Soup export", "dux_soup",
         ["VisitTime", "SalesProfile", "CompanyProfile", "CompanyWebsite", "Middle Name", "Degree", "Picture"],
         ["Profile", "First Name", "Last Name", "Title", "Company", "Location", "Industry", "Email", "Phone"],
         {"Profile": "linkedin_url", "CompanyProfile": "company_linkedin_url"}),
    _fmt("zoominfo", "ZoomInfo export", "zoominfo",
         ["ZoomInfo Contact ID", "ZoomInfo Company ID", "Direct Phone Number", "Management Level",
          "Job Title Hierarchy Level", "Company HQ Phone", "Primary Industry", "Employee Range",
          "LinkedIn Contact Profile URL", "Job Function", "Person City", "Person State"],
         ["First Name", "Last Name", "Job Title", "Email Address", "Company Name", "Website", "Mobile phone", "Country"]),
    _fmt("hunter", "Hunter export", "hunter",
         ["Email address", "Position", "Domain name", "Organization", "Confidence score", "Number of sources",
          "Twitter handle", "Department", "Seniority"],
         ["First name", "Last name", "Phone number", "LinkedIn URL"]),
    _fmt("snov", "Snov.io export", "snov", ["Social URL", "Prospect status", "Snov"],
         ["First name", "Last name", "Full name", "Position", "Company name", "Company URL", "Email", "Email status",
          "Industry", "Location", "Country"]),
    _fmt("seamless", "Seamless.AI export", "seamless",
         ["Email 1 Validation", "Email 1 Total AI", "Contact Phone 1", "Company Phone 1", "Contact LI Profile URL",
          "Company LI Profile Url", "Company Staff Count", "Company Staff Count Range", "Contact Country"],
         ["First Name", "Last Name", "Title", "Company", "Email 1", "Website", "Industry"]),
    _fmt("rocketreach", "RocketReach export", "rocketreach", ["Email Grade", "Country Code", "Work Phone", "Employer"],
         ["Name", "Title", "Company", "Company Domain", "Email", "Personal Email", "Mobile Phone", "LinkedIn", "Location"]),
    _fmt("hubspot", "HubSpot contacts export", "hubspot",
         ["Record ID", "Phone Number", "Mobile Phone Number", "Website URL", "State/Region", "Country/Region",
          "Lifecycle Stage", "Unsubscribed from all email", "Marketing contact status", "Associated Company"],
         ["First Name", "Last Name", "Email", "Job Title", "Company Name", "City", "Lead Status", "Contact owner"]),
    _fmt("salesforce", "Salesforce contact / lead report", "salesforce",
         ["Company / Account", "Account Name", "Mailing City", "Mailing State/Province", "Mailing Country",
          "State/Province", "Lead Owner", "Email Opt Out", "Do Not Call", "Salutation"],
         ["First Name", "Last Name", "Title", "Email", "Phone", "Mobile", "Lead Source", "Lead Status", "Contact Owner"]),
    _fmt("zoho_crm", "Zoho CRM leads export", "zoho_crm",
         ["Record Id", "Lead Owner", "Email Opt Out", "Secondary Email", "Created Time", "Skype ID", "Zip Code"],
         ["Company", "First Name", "Last Name", "Title", "Email", "Phone", "Mobile", "Lead Source", "Lead Status"]),
    _fmt("pipedrive", "Pipedrive people export", "pipedrive",
         ["Person - Name", "Person - Email - Work", "Person - Phone - Work", "Person - Phone - Mobile",
          "Person - Organization", "Person - Job title", "Person - Owner", "Organization - Name"],
         ["Person - Label", "Organization - Address"]),
    _fmt("job_board", "Job board export (Naukri / Indeed / Shine / Apna)", "job_board",
         ["Posted On", "Date Posted", "Openings", "Job Link", "Job URL", "Experience", "Salary", "Skills", "Key Skills",
          "Job ID", "Vacancies", "Job Description", "Apply Link"],
         ["Company Name", "Job Title", "Location", "Company", "Title"],
         {"Job Title": "role", "Title": "role", "Position": "role", "Designation": "role", "Role": "role",
          "URL": "job_link"}),
    _fmt("directory", "Business directory (IndiaMART / JustDial style)", "directory",
         ["Firm Name", "Mobile No.", "GST", "GSTIN", "GST No", "Pincode", "Pin Code", "Business Category",
          "Nature of Business", "Year of Establishment", "Deals In", "Landline"],
         ["Name", "Contact Person", "Address", "City", "Category", "Phone", "Email", "Website"],
         {"Name": "company"}),
    _fmt("google_maps", "Google Maps / Places export", "google_maps",
         ["Place ID", "Google Maps URL", "Plus Code", "Latitude", "Longitude", "Reviews Count", "Reviews", "Rating",
          "Main Category", "Categories", "Total Score", "Place URL", "CID", "Maps URL", "Opening Hours", "Category"],
         ["Name", "Title", "Phone", "Website", "Address", "City", "Street", "Postal Code"],
         {"Name": "company", "Title": "company", "Category": "sector", "Main Category": "sector",
          "Categories": "sector"}),
    _fmt("outreach_csv", "Outreach OS CSV", "manual",
         ["contact_name", "open_roles", "company_size", "max_job_age_days", "repost_count"],
         ["company", "title", "email", "phone", "website", "country"]),
)
FORMAT_BY_KEY = {f["key"]: f for f in FORMATS}


def detect_format(headers: list) -> dict:
    """The best-fitting known layout, or an 'Unrecognised layout' keyed by its column set (so a saved mapping applies)."""
    present = {loose_header(h) for h in headers}
    best, best_score = None, 0
    for fmt in FORMATS:
        strong, weak = len(fmt["strong"] & present), len(fmt["weak"] & present)
        score = 2 * strong + weak
        if strong and score >= 4 and score > best_score:
            best, best_score = fmt, score
    if best:
        return {"key": best["key"], "name": best["name"], "source": best["source"], "matched_columns": best_score}
    shape = hashlib.sha1(",".join(sorted(present)).encode("utf-8")).hexdigest()[:10]
    return {"key": f"custom:{shape}", "name": "Unrecognised layout", "source": "", "matched_columns": 0}


def _keyword_field(loose: str):
    words = set(loose.split())
    for needed, banned, field in KEYWORD_RULES:
        if needed <= words and not words & banned:
            return field
    return None


def _fuzzy_field(loose: str):
    if len(loose) < 4:
        return None, 0.0
    match = get_close_matches(loose, list(ALIAS), n=1, cutoff=0.84)
    if not match:
        return None, 0.0
    field, conf = ALIAS[match[0]]
    return field, round(min(0.85, SequenceMatcher(None, loose, match[0]).ratio() * conf * 0.9), 2)


def sniff_values(samples: list):
    """What the values look like, whatever the header says: email, phone, LinkedIn or website columns."""
    values = [clean_text(v) for v in samples if clean_text(v)][:25]
    if len(values) < 2:
        return None
    share = lambda test: sum(1 for v in values if test(v)) / len(values)
    if share(lambda v: "linkedin.com/company/" in v.lower()) >= 0.6:
        return "company_linkedin_url"
    if share(lambda v: "linkedin.com/in/" in v.lower()) >= 0.6:
        return "linkedin_url"
    if share(lambda v: clean_email(re.split(r"[,;\s]+", v)[0])[0] is not None) >= 0.6:
        return "email"
    if share(lambda v: bool(parse_phones(v)["numbers"]) and sum(c.isalpha() for c in v) <= 3) >= 0.6:
        return "phone"
    if share(lambda v: " " not in v and "@" not in v and bool(host_of(v))) >= 0.6:
        return "website"
    if share(lambda v: bool(re.fullmatch(r"[A-Za-z][A-Za-z.' ]{1,40}", v))) >= 0.6:
        return "words"
    return None


VALUE_FIELDS = frozenset({"email", "phone", "linkedin_url", "company_linkedin_url", "website", "job_link"})


def map_columns(headers: list, sample_rows: list, fmt: dict, saved: dict = None, overrides: dict = None) -> list:
    """[{column, field, confidence, how, samples}] for each header, in file order. field None means not imported."""
    rules = FORMAT_BY_KEY.get(fmt.get("key"), {}).get("overrides", {})
    saved, overrides = saved or {}, overrides or {}
    out = []
    for header in headers:
        loose = loose_header(header)
        samples = [clean_text(r.get(header)) for r in sample_rows if clean_text(r.get(header))]
        field, conf, how = None, 0.0, ""
        if header in overrides:
            choice = overrides[header]
            field, conf, how = (None if choice == "ignore" else choice), 1.0, "you chose"
        elif loose in saved:
            field, conf, how = (None if saved[loose] == "ignore" else saved[loose]), 1.0, "saved mapping"
        elif loose in rules:
            field, conf, how = rules[loose], 0.98, f"{fmt['name']} rule"
        elif loose in ALIAS:
            field, conf = ALIAS[loose]
            how = "known name"
            if norm_header(header) not in EXACT:
                conf, how = conf * 0.9, "known name (loose match)"
        elif loose in EXTRA:
            how = "extra column, not lead data"
        elif (kw := _keyword_field(loose)):
            field, conf, how = kw, 0.8, "keyword in name"
        else:
            field, conf = _fuzzy_field(loose)
            how = "similar name" if field else ""
        if how not in ("you chose", "saved mapping", "extra column, not lead data"):
            sniffed = sniff_values(samples)
            if sniffed == "phone" and not field and "id" in loose.split():
                sniffed = None
            if sniffed == "words" and field in ("phone", "email"):
                person = {"contact", "person", "name"} & set(loose.split())
                field, conf = ("contact_name", 0.6) if person else (None, 0.0)
                how = f"{how}; values do not look like {'phone numbers' if loose and 'mail' not in loose else 'emails'}"
            elif sniffed and sniffed != "words" and sniffed != field and field not in VALUE_FIELDS - {"website"} and not (sniffed == "website" and field):
                note = f"values look like {FIELDS[sniffed].lower()}"
                field, conf, how = sniffed, (0.8 if field else 0.7), (f"{how}; {note}" if how else note)
        out.append({"column": header, "field": field, "confidence": round(conf, 2), "how": how, "samples": samples[:3]})
    return out
