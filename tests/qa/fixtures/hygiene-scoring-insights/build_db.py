"""Builds the 500-lead QA store for the hygiene / scoring / Today / Insights QA run. Deterministic; all data fictional.

    python tests/qa/fixtures/hygiene-scoring-insights/build_db.py

generate() returns (store collections, planted truth); the tests call it directly, so no large JSON is committed.
Running the file writes store_500.json and truth.json next to it for a look by hand (they are git-ignored).

Leads come in the two shapes a real store holds:
- "legacy": saved by the quick paste or older versions: company as typed (ALL CAPS, "Pvt. Ltd."), honorifics, raw titles,
  raw city spellings (Bangalore, Bombay, Gurgaon, Poona), country as typed ("India"), phones in their original formats.
- "smart": saved by the smart import / campaigns: cleaned with the app's own rules (company_legal, state, IN, e164).
Domains are *.example / *.invalid, phones are 90000 xxxxx style, and every gmail local part carries a "qa" marker.
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.hygiene import business_domain, dedupe_key, normalise_phone  # noqa: E402
from app.places import city_key, clean_city, clean_country, is_indian_place, size_bucket, state_for_city  # noqa: E402
from app.standardise import (best_phone, clean_company, clean_person_name, clean_title, company_key,  # noqa: E402
                             department_of, key_similarity, parse_phone, seniority_of, website_domain)

SEED = 20261103
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)          # Tue 11:30 IST
TOTAL = 500
rng = random.Random(SEED)

FIRST = ["Ravi", "Anita", "Sanjay", "Meena", "Arjun", "Priyanka", "Deepak", "Kavita", "Rahul", "Sunita", "Vikram", "Neha",
         "Amit", "Pooja", "Rohit", "Swati", "Manoj", "Asha", "Karan", "Divya", "Suresh", "Lakshmi", "Imran", "Farah",
         "Gurpreet", "Harleen", "Joseph", "Mary", "Venkat", "Padma", "Abhishek", "Shalini", "Nitin", "Rekha", "Tarun",
         "Bhavna", "Yusuf", "Zoya", "Mohan", "Geeta"]
LAST = ["Kumar", "Sharma", "Iyer", "Rao", "Das", "Patel", "Singh", "Gupta", "Reddy", "Nair", "Khan", "Mehta", "Joshi",
        "Menon", "Bose", "Chatterjee", "Pillai", "Verma", "Yadav", "Agarwal", "Kulkarni", "Desai", "Shaikh", "Fernandes",
        "Gill", "Bhat", "Naidu", "Saxena", "Mishra", "Pandey"]
TITLES_RAW = ["head - human resources", "HR MANAGER", "Sr. HR Exec", "Talent Acquisition Lead", "FOUNDER/CEO", "Director",
              "HRBP", "Manager - TA", "Proprietor", "Co-founder", "VP - Human Resources", "Recruiter", "Operations Head"]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Chennai", "Hyderabad", "Delhi", "Noida", "Gurugram", "Kolkata", "Ahmedabad",
          "Jaipur", "Lucknow", "Indore", "Coimbatore", "Kochi", "Nagpur", "Surat", "Chandigarh"]
LEGACY_CITY = {"Mumbai": "Bombay", "Bengaluru": "Bangalore", "Gurugram": "Gurgaon", "Pune": "Poona", "Chennai": "Madras",
               "Kolkata": "Calcutta", "Kochi": "Cochin", "Delhi": "New Delhi"}
PREFIX = ["Ganesh", "Laxmi", "Kaveri", "Narmada", "Shivam", "Arihant", "Mahavir", "Gokul", "Sagar", "Surya", "Chandra",
          "Indus", "Ashoka", "Vindhya", "Triveni", "Sahyadri", "Nilgiri", "Malabar", "Konark", "Ajanta", "Ellora",
          "Godavari", "Tapti", "Yamuna", "Saraswati", "Shakti", "Prerna", "Utkarsh", "Navkar", "Siddhi", "Tulsi",
          "Kesari", "Neelkanth", "Omkar", "Pragati", "Samarth", "Vaibhav", "Aarav", "Ishaan", "Vihaan", "Anvay",
          "Darsh", "Kiyan", "Ojas", "Pranav", "Reyansh", "Shaurya", "Tanmay", "Hemkund", "Jaldhara"]
MIDDLE = ["", "Agro", "Steel", "Auto", "Pharma", "Foods", "Tex", "Poly", "Infra", "Digital", "Health", "Edu", "Fin",
          "Print", "Chem", "Power", "Marine", "Leather", "Glass", "Paper"]
SECTORS = {"Manufacturing": ["Industries", "Engineering Works", "Castings"], "Retail": ["Retail", "Mart", "Stores"],
           "Logistics": ["Logistics", "Freight Carriers", "Movers"], "IT Services": ["Infotech", "Softlabs", "Digital Services"],
           "BPO": ["BPO Services", "Call Solutions", "CX Services"], "Healthcare": ["Hospitals", "Diagnostics", "Clinics"],
           "Education": ["Academy", "Edutech", "Learning Centre"], "Hospitality": ["Hotels", "Caterers", "Resorts"],
           "BFSI": ["Finserv", "Capital", "Microfinance"], "Real Estate": ["Realty", "Developers", "Builders"]}
SOURCES = ["apollo", "naukri", "indiamart", "justdial", "google_maps", "bmi_sheet:leads", "campaign:c1", "manual",
           "salesnav"]
STAGE_WEIGHTS = {"new": 40, "verified": 5, "enriched": 6, "contacted": 18, "engaged": 5, "interested": 4,
                 "whatsapp_active": 2, "call_done": 3, "trial": 2, "customer": 2, "advocate": 1, "not_now": 4,
                 "unsubscribed": 4, "disqualified": 4}

store = {k: [] for k in ("leads", "outbox", "tasks", "inbox", "whatsapp_outbox", "suppressed", "activities",
                         "transactional_outbox", "consent_ledger")}
truth = {"seed": SEED, "now": NOW.isoformat(), "groups": [], "negatives": [], "planted": {}}
taken_keys, used_keys, used_domains = set(), [], set()
counter = {"id": 0, "mob": 0, "grp_mob": 0, "gmail": 0, "task": 0, "msg": 0}


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def new_id() -> str:
    counter["id"] += 1
    return f"q{counter['id']:03d}"


def mobile(group: bool = False) -> str:
    key = "grp_mob" if group else "mob"
    counter[key] += 1
    n = counter[key]
    return f"+91 90000 {'2' if group else '1'}{n:04d}"


def gmail(first: str, last: str) -> str:
    counter["gmail"] += 1
    return f"{first}.{last}.qa{counter['gmail']:03d}@gmail.com".lower()


def person() -> tuple:
    return rng.choice(FIRST), rng.choice(LAST)


used_people = set()


def unique_person() -> tuple:
    """A name no other planted group uses, so groups never link to each other by accident."""
    while True:
        pair = person()
        if pair not in used_people:
            used_people.add(pair)
            return pair


def register(company: str, city: str, domain: str = "") -> None:
    used_keys.append((company_key(company), city_key(city)))
    if domain:
        used_domains.add(domain)


def clashes(company: str, city: str) -> bool:
    key, ck = company_key(company), city_key(city)
    for other, other_city in used_keys:
        if other == key:
            return True
        if (not ck or not other_city or ck == other_city) and other[:3] == key[:3] and key_similarity(key, other) >= 0.85:
            return True
    return False


def build(company="", contact="", title="", email=None, phones=(), website="", city="", country="", source="apollo",
          shape="smart", days=None, sector="", size=0, open_roles=0, job_age=0, reposts=0, raw_phone_list=False,
          **extra) -> dict:
    lid = new_id()
    created = NOW - timedelta(days=days if days is not None else rng.randint(3, 200), hours=rng.randint(0, 9))
    at = iso(created)
    lead = {"id": lid, "company": "", "contact_name": "", "title": "", "email": email, "email_is_role": False,
            "phone": None, "website": "", "domain": None, "country": "", "source": source, "stage": "new",
            "consent": None, "engagement": {"opens": 0, "clicks": 0, "replies": 0},
            "timeline": [{"at": at, "kind": "created", "text": f"Imported from {source}"}], "created_at": at,
            "open_roles": open_roles, "company_size": size, "max_job_age_days": job_age, "repost_count": reposts}
    if email:
        local = email.split("@")[0]
        lead["email_is_role"] = local in ("hr", "info", "careers", "jobs", "contact", "sales", "admin")
    if shape == "legacy":
        lead.update(company=company, contact_name=contact, title=title, website=website, country=country)
        lead["phone"] = normalise_phone(phones[0]) if phones else None
        if raw_phone_list:
            lead["phones"] = list(phones)
        lead["domain"] = business_domain(website) or business_domain(email)
        if city:
            lead["city"] = city
        if sector:
            lead["sector"] = sector
    else:
        display, legal = clean_company(company)
        name = clean_person_name(contact)
        clean_t = clean_title(title)
        numbers = [found for found, _ in (parse_phone(p) for p in phones) if found]
        phone, kind = best_phone(numbers)
        domain, site, _ = website_domain(website)
        if not domain and email and not email.endswith(("@gmail.com", "@googlemail.com")):
            domain = email.rsplit("@", 1)[1]
            site = f"https://{domain}"
        clean_c = clean_city(city)
        ctry = clean_country(country) or ("IN" if is_indian_place(clean_c) or (numbers and all(
            n[0].startswith("+91") for n in numbers)) else "")
        lead.update(company=display, contact_name=name, title=clean_t, phone=phone, website=site or "",
                    domain=domain or None, country=ctry)
        for key, value in (("company_legal", legal), ("seniority", seniority_of(clean_t)),
                           ("department", department_of(clean_t)), ("phone_type", kind or ""),
                           ("phones", [n[0] for n in numbers]), ("city", clean_c), ("state", state_for_city(clean_c)),
                           ("sector", sector), ("company_size_bucket", size_bucket(size)),
                           ("emails", [email] if email else [])):
            if value:
                lead[key] = value
        lead["field_sources"] = {f: source for f in ("company", "contact_name", "email", "phone", "city")
                                 if lead.get(f)}
    lead.update(extra)
    key = dedupe_key(lead) or f"company:{company_key(lead['company'])}|{city_key(lead.get('city'))}"
    lead["dedupe_key"] = key if key not in taken_keys else f"{key}#{lid}"
    taken_keys.add(lead["dedupe_key"])
    store["leads"].append(lead)
    return lead


def ev(lead: dict, days_after: float, kind: str, text: str) -> str:
    start = datetime.fromisoformat(lead["created_at"])
    at = iso(min(start + timedelta(days=days_after), NOW - timedelta(hours=2)))
    lead["timeline"].append({"at": at, "kind": kind, "text": text})
    return at


def add_task(lead_id, kind, title, due: datetime, priority="normal", status="open", text=""):
    counter["task"] += 1
    store["tasks"].append({"id": f"t{counter['task']:03d}", "lead_id": lead_id, "kind": kind, "title": title,
                           "text": text, "due_at": iso(due.astimezone(timezone.utc)), "owner": "Priya",
                           "priority": priority, "status": status, "done": status == "done", "source": "qa",
                           "created_at": iso(due - timedelta(hours=3)), "at": iso(due - timedelta(hours=3))})


def emailed(lead: dict, steps=(0, 3), status="sent") -> None:
    start = datetime.fromisoformat(lead["created_at"]) + timedelta(days=1)
    lead["sequence"] = {"enrolled_at": iso(start), "sent_days": [], "stopped": None}
    for day in steps:
        when = min(start + timedelta(days=day), NOW - timedelta(hours=3))
        counter["msg"] += 1
        store["outbox"].append({"lead_id": lead["id"], "to": lead["email"], "step": f"day {day}", "status": status,
                                "subject": "Screening for your open roles", "sent_at": iso(when),
                                "sent_at_ist_date": (when + timedelta(hours=5, minutes=30)).date().isoformat(),
                                "message_id": f"<qa{counter['msg']:04d}.{lead['id']}@mail.outreach.example>"})
        if status == "sent":
            lead["sequence"]["sent_days"].append(day)
            lead["sequence"]["last_sent_at"] = iso(when)
            ev(lead, 1 + day, "email", f"day {day} — sent")


def replied(lead: dict, text: str, label: str, after: float = 6) -> str:
    lead["engagement"] = {**lead["engagement"], "replies": lead["engagement"]["replies"] + 1}
    at = ev(lead, after, "reply", f"[{label}] (email, imap) {text}")
    store["inbox"].append({"id": f"in-{lead['id']}-{len(store['inbox'])}", "lead_id": lead["id"],
                           "from": lead.get("email") or "", "text": text, "label": label, "received_at": at})
    return at


def consent(lead: dict, text: str, after: float = 6) -> None:
    at = ev(lead, after, "consent", f"Said yes: {text}")
    lead["consent"] = {"text": text, "at": at, "source": "email reply", "channel": "any"}
    store["consent_ledger"].append({"id": f"cl-{lead['id']}-g", "lead_id": lead["id"], "event": "given",
                                    "channel": "any", "text": text, "source": "email reply", "at": at})


def life(lead: dict, stage: str) -> dict:
    """Give the lead the history a lead in this stage really has: emails, replies, consent, tasks, outbox rows."""
    lead["stage"] = stage
    if stage in ("new", "verified", "enriched"):
        if stage != "new":
            ev(lead, 1, "stage", f"new → {stage}")
        return lead
    from_sheet = lead["source"].startswith("bmi_sheet")
    if lead.get("email") and not from_sheet:
        emailed(lead, (0, 3) if stage != "contacted" else rng.choice([(0,), (0, 3), (0, 3, 7)]))
        lead["engagement"]["opens"] = rng.randint(0, 3)
    if stage == "contacted":
        return lead
    if stage in ("engaged",):
        replied(lead, "Tell me more, what does it cost?", "question")
    elif stage == "not_now":
        replied(lead, "Not right now, maybe next quarter", "not_now")
        if lead.get("sequence"):
            lead["sequence"]["stopped"] = "replied"
    elif stage == "disqualified":
        replied(lead, "Not interested, thanks", "not_interested")
    elif stage == "unsubscribed":
        at = replied(lead, "Please remove me from your list", "unsubscribe")
        lead["consent_withdrawn_at"] = at
        if lead.get("sequence"):
            lead["sequence"]["stopped"] = "unsubscribed"
        if lead.get("email"):
            store["suppressed"].append(lead["email"])
        store["consent_ledger"].append({"id": f"cl-{lead['id']}-w", "lead_id": lead["id"], "event": "withdrawn",
                                        "channel": "email", "text": "Please remove me from your list",
                                        "source": "email reply (imap)", "at": at})
    else:                                                       # said yes at some point
        if not from_sheet:
            replied(lead, "Yes please, send the sample screen", "interested")
        consent(lead, "Yes please, send the sample screen" if not from_sheet else "Yes on the demo call with Priya")
        lead["consent_yes_note"] = True
        if stage == "whatsapp_active":
            lead["whatsapp_last_inbound_at"] = ev(lead, 8, "whatsapp", "Haan, details bhejiye")
            store["whatsapp_outbox"].append({"lead_id": lead["id"], "template": "first_after_yes", "status": "dry_run",
                                             "to": lead.get("phone") or "", "sent_at": ev(lead, 8.1, "whatsapp",
                                                                                           "first_after_yes — dry_run")})
        if stage in ("call_done", "trial", "customer", "advocate"):
            ev(lead, 9, "call", "Call outcome: interested in a free role")
        if stage == "trial":
            since = iso(NOW - timedelta(days=7))
            lead["activation"] = {"step": "published", "events": [], "usage": {"candidates": 0, "bookings": 0},
                                  "since": since, "started": since}
        if stage in ("customer", "advocate"):
            lead["activation"] = {"step": "converted", "events": [], "usage": {"candidates": 22, "bookings": 5},
                                  "since": iso(NOW - timedelta(days=12))}
    lead.pop("consent_yes_note", None)
    return lead


def group(kind: str, expect: str, leads: list, note: str) -> list:
    truth["groups"].append({"kind": kind, "expect": expect, "lead_ids": [l["id"] for l in leads], "note": note})
    return leads


# ---------------------------------------------------------------- A: same email (12 groups)
def plant_same_email():
    stage_pairs = [("engaged", "unsubscribed"), ("new", "disqualified"), ("call_done", "customer"),
                   ("customer", "disqualified"), ("contacted", "new"), ("new", "new"), ("enriched", "contacted"),
                   ("new", "verified"), ("not_now", "new"), ("new", "contacted"), ("new", "new", "new"),
                   ("contacted", "new", "enriched")]
    names = [("Kaveri", "Retail"), ("Ellora", "Freight Carriers"), ("Narmada", "Hospitals"), ("Hemkund", "Caterers"),
             ("Tapti", "Polymers"), ("Gokul", "Dairy Foods"), ("Sagar", "Marine Exports"), ("Vindhya", "Castings"),
             ("Konark", "Realty"), ("Nilgiri", "Tea Estates"), ("Ajanta", "Pharma"), ("Triveni", "Learning Centre")]
    for n, ((brand, word), stages) in enumerate(zip(names, stage_pairs)):
        first, last = unique_person()
        domain = f"{brand.lower()}{word.split()[0].lower()}.example"
        email = f"{first}.{last}@{domain}".lower()
        city = CITIES[n % len(CITIES)]
        register(f"{brand} {word}", city, domain)
        a = build(f"{brand} {word} Pvt Ltd", f"{first} {last}", "head - human resources", email,
                  [mobile(group=True)], f"https://{domain}", city, "India", "apollo", sector="Retail", size=180,
                  open_roles=12, job_age=40)
        b = build(f"{brand} {word} Pvt. Ltd.".upper(), f"MR. {first} {last}".upper(), "HR MANAGER", email, [],
                  "", LEGACY_CITY.get(city, city), "India", rng.choice(["indiamart", "manual", "naukri"]),
                  shape="legacy", size=200)
        members = [a, b]
        if len(stages) == 3:
            members.append(build(f"{brand} {word}", f"{first} {last[0]}.", "Talent Acquisition Lead", email, [],
                                 f"https://careers.{domain}", "", "IN", "campaign:c1", campaign_id="c1",
                                 open_roles=8))
        for lead, stage in zip(members, stages):
            life(lead, stage)
        group("same_email", "cluster", members, f"{brand} {word}: one address saved by several sources")


# ---------------------------------------------------------------- B: Gmail dots / +tags / googlemail (8 groups)
def plant_gmail_alias():
    stage_pairs = [("interested", "contacted"), ("new", "new"), ("contacted", "new"), ("new", "engaged"),
                   ("new", "new", "new"), ("unsubscribed", "new"), ("new", "verified"), ("enriched", "new")]
    for n, stages in enumerate(stage_pairs):
        first, last = unique_person()
        base = gmail(first, last)                       # first.last.qaNNN@gmail.com
        local = base.split("@")[0]
        variants = [base, f"{local.replace('.', '')}+naukri@gmail.com", f"{local}@googlemail.com"]
        city = CITIES[(n + 5) % len(CITIES)]
        register(f"{first} {last} Enterprises", city)
        members = []
        for i, stage in enumerate(stages):
            members.append(life(build(f"{first} {last} Enterprises" if i == 0 else f"{first.upper()} {last.upper()} ENTERPRISES",
                                      f"{first} {last}" if i != 1 else f"{first.upper()} {last.upper()}",
                                      "Proprietor", variants[i], [], "", city if i == 0 else "", "",
                                      ["naukri", "justdial", "apna"][i], shape="legacy" if i else "smart"), stage))
        group("gmail_alias", "cluster", members, "personal Gmail written with dots, a +tag or googlemail.com")


# ---------------------------------------------------------------- C: same phone, different formats (10 groups)
def plant_same_phone():
    for n in range(10):
        first, last = unique_person()
        num = mobile(group=True)                       # '+91 90000 2NNNN'
        digits = num.replace(" ", "")[3:]
        formats = [num, f"0{digits[:5]} {digits[5:]}", f"{digits}", f"+91-{digits[:5]}-{digits[5:]}"]
        city = CITIES[(n + 2) % len(CITIES)]
        company_a = f"{last} Packaging Industries" if n % 2 else f"{last} Garments"
        company_b = f"{PREFIX[(n * 3 + 11) % len(PREFIX)]} {['Exim', 'Overseas', 'Agencies'][n % 3]}"   # trade name
        register(company_a, city)
        register(company_b, "")
        a = build(company_a, f"{first} {last}", "Proprietor", None, [formats[0]], "", city, "", "justdial",
                  sector="Manufacturing", size=35)
        b = build(company_b.upper(), f"{first[0]}. {last}" if n % 3 else "", "", None, [formats[1]], "", "", "India",
                  "indiamart", shape="legacy", raw_phone_list=True)
        members = [a, b]
        if n < 3:
            members.append(build("", f"{first} {last}".upper(), "OWNER", gmail(first, last), [formats[2 + n % 2]],
                                 "", "", "", "naukri", shape="legacy", raw_phone_list=True))
        for lead in members:
            life(lead, "new")
        group("same_phone", "cluster", members, "one mobile written as +91 …, 0…, bare digits or with dashes")
    # C2: a family number shared by two people; the second person's two leads must still be grouped (order test)
    num = mobile(group=True)
    digits = num.replace(" ", "")[3:]
    register("Rao Textile Mills", "Surat")
    register("Shree Rao Fabrics", "Surat")
    register("Rao Fabrics", "")
    dev = life(build("Rao Textile Mills", "Dev Rao", "Owner", None, [num], "", "Surat", "", "justdial"), "new")
    anita = life(build("Shree Rao Fabrics", "Anita Rao", "Director", None, [f"0{digits[:5]} {digits[5:]}"], "", "Surat",
                       "", "indiamart"), "new")
    a_rao = life(build("RAO FABRICS", "A. RAO", "", None, [f"+91-{digits[:5]}-{digits[5:]}"], "", "", "India",
                       "manual", shape="legacy", raw_phone_list=True), "new")
    group("same_phone_second_person", "cluster", [anita, a_rao],
          "Anita Rao's two leads share the family number that Dev Rao (saved first) also uses")
    truth["negatives"].append({"kind": "shared_family_phone_other_person", "lead_ids": [dev["id"], anita["id"]],
                               "note": "Dev and Anita Rao share a phone but are different people at different firms"})


# ---------------------------------------------------------------- D: same company domain, different named people
def plant_same_domain_people():
    for n in range(8):
        brand = PREFIX[(n * 5 + 3) % len(PREFIX)]
        domain = f"{brand.lower()}infra{n}.example"
        city = CITIES[(n + 7) % len(CITIES)]
        (f1, l1), (f2, l2) = unique_person(), unique_person()
        while f2 == f1:
            f2 = rng.choice(FIRST)
        register(f"{brand} Infra Projects", city, domain)
        a = build(f"{brand} Infra Projects Pvt Ltd", f"{f1} {l1}", "HR Head", f"{f1}.{l1}@{domain}".lower(),
                  [mobile(group=True)], f"https://{domain}", city, "IN", "apollo", sector="Real Estate", size=400)
        # n < 5: the second contact's row carries the company name; n >= 5: a Hunter-style row with only name + email
        b = build(f"{brand} Infra Projects" if n < 5 else "", f"{f2} {l2}", "Founder", f"{f2.lower()}@{domain}", [],
                  "", city if n < 5 else "", "", "salesnav")
        for lead in (a, b):
            life(lead, rng.choice(["new", "contacted"]))
        group("same_domain_two_people", "company_duplicate", [a, b],
              "two different people at one company domain (an import would keep the second as another contact)"
              + ("" if n < 5 else "; second row has no company name"))


# ---------------------------------------------------------------- E: company-only lead + person lead, same domain
def plant_company_only():
    for n in range(8):
        brand = PREFIX[(n * 3 + 20) % len(PREFIX)]
        word = ["Diagnostics", "Hotels", "Academy", "Castings", "Logistics", "Mart", "Finserv", "Softlabs"][n]
        domain = f"{brand.lower()}{word.lower()}.invalid"
        city = CITIES[(n + 11) % len(CITIES)]
        first, last = unique_person()
        register(f"{brand} {word}", city, domain)
        maps = build(f"{brand} {word}", "", "", None, [f"+91 11 4000 {7000 + n}"], f"https://www.{domain}/contact",
                     city, "", "google_maps", phone_type="business", sector="Healthcare", size=60)
        site = f"https://careers.{domain}" if n % 2 else f"https://{domain}"
        person_lead = build(f"{brand} {word} Private Limited".upper(), f"{first} {last}", "HRBP",
                            f"{first.lower()}@{domain}", [], site, LEGACY_CITY.get(city, city) if n % 3 else "",
                            "India", "apollo", shape="legacy", size=65)
        life(maps, "new")
        life(person_lead, ["new", "contacted", "trial", "engaged"][n % 4])
        group("company_only_same_domain", "cluster", [maps, person_lead],
              "a Google Maps company row and a person at the same domain (one on a careers. sub-domain)")


# ---------------------------------------------------------------- F: same person at one domain, two addresses
def plant_same_person_two_addresses():
    for n in range(5):
        brand = PREFIX[(n * 9 + 2) % len(PREFIX)]
        domain = f"{brand.lower()}-tech{n}.example"
        first, last = unique_person()
        register(f"{brand} Tech Labs", "", domain)
        a = build(f"{brand} Tech Labs", f"{first} {last}", "TA Lead", f"{first}.{last}@{domain}".lower(), [],
                  f"https://{domain}", "", "", "apollo")
        b = build("", f"{last.upper()}, {first.upper()}" if n % 2 else f"{first} {last}", "Talent Acquisition",
                  f"{first[0]}{last}@{domain}".lower(), [], "", "", "", "salesnav", shape="legacy")
        life(a, "contacted")
        life(b, "new")
        group("same_domain_same_person", "cluster", [a, b], "one person, two addresses at the company domain")


# ---------------------------------------------------------------- G: fuzzy company names in one city
FUZZY = [("Acme Retail Pvt Ltd", "Mumbai", "ACME RETAIL", "Bombay"),
         ("Sharma Logistics Pvt. Ltd.", "Pune", "Sharma Logistic", "Poona"),
         ("Orion Softworks Private Limited", "Bengaluru", "ORION SOFTWORK", "Bangalore"),
         ("M/s. Laxmi Steel Works", "Nagpur", "Laxmi Steel Works (OPC) Pvt Ltd", "Nagpur"),
         ("Kaveri Agro Foods LLP", "Coimbatore", "Kaveri Agro Food", "Coimbatore"),
         ("The Hindustan Packaging Co.", "Kolkata", "Hindusthan Packaging Co", "Calcutta"),
         ("Sunrise Hospitality & Services", "Jaipur", "Sunrise Hospitality and Services", "Jaipur"),
         ("Ganesh Auto Components Pvt Ltd", "Gurugram", "GANESH AUTO COMPONENT", "Gurgaon"),
         ("Nirmal Healthcare Solutions", "Hyderabad", "Nirmal Healthcare", "Hyderabad"),
         ("Pearl Edutech Pvt Ltd", "Kochi", "Pearl Edu-Tech", "Cochin"),
         ("Vardhman Spinning Mills", "Ludhiana", "Vardhaman Spinning Mills", "Ludhiana"),
         ("Royal Caterers", "Lucknow", "ROYAL CATERERS", "")]


def plant_fuzzy_names():
    for n, (name_a, city_a, name_b, city_b) in enumerate(FUZZY):
        register(name_a, city_a)
        (f1, l1), (f2, l2) = unique_person(), unique_person()
        a = build(name_a, f"{f1} {l1}", "HR Manager", None, [mobile(group=True)], "", city_a, "IN",
                  "google_maps" if n % 2 else "apollo", size=90, open_roles=4)
        b = build(name_b, f"{f2} {l2}" if n % 3 else f"{f1} {l1}", "Director", None, [], "", city_b, "India", "justdial",
                  shape="legacy")
        life(a, "contacted" if n % 4 == 0 else "new")
        life(b, "new")
        group("fuzzy_company_same_city", "cluster", [a, b], f"{name_a} ({city_a}) vs {name_b} ({city_b or 'no city'})")


# ---------------------------------------------------------------- H: real duplicates the rules are not built to catch
HARD = [("Sri Venkateswara Traders", "Shri Venkateswara Traders", "Chennai", "Sri/Shri spelling: first letters differ"),
        ("Shree Balaji Enterprises", "Shri Balaji Enterprise", "Indore", "two small spelling differences (91%)"),
        ("Om Sai Ram Packers", "Om Sai Ram Packers and Movers", "Thane", "extra non-generic words"),
        ("Dr. Lal Diagnostics", "Lal Diagnostics", "Delhi", "a leading 'Dr.' changes the first letters")]


def plant_hard_positives():
    for name_a, name_b, city, why in HARD:
        register(name_a, city)
        register(name_b, city)
        a = life(build(name_a, "", "", None, [mobile(group=True)], "", city, "", "justdial"), "new")
        b = life(build(name_b.upper(), "", "", None, [], "", city, "India", "indiamart", shape="legacy"), "new")
        group("hard_positive", "known_miss", [a, b], why)


# ---------------------------------------------------------------- negatives that must not be grouped
def plant_negatives():
    nimbus = [life(build("Nimbus Foods", f"{f} {l}", "HR Manager", None, [mobile(group=True)], "", city, "", "apollo"),
                   "new") for (f, l), city in zip([person() for _ in range(3)], ("Pune", "Chennai", "Jaipur"))]
    register("Nimbus Foods", "")
    truth["negatives"].append({"kind": "same_name_other_city", "lead_ids": [l["id"] for l in nimbus],
                               "note": "three different 'Nimbus Foods' in three cities"})
    sai = [life(build("Sai Enterprises", "", "", None, [mobile(group=True)], "", city, "", "google_maps"), "new")
           for city in ("Pune", "Lucknow", "Surat", "Indore")]
    sai_blank = life(build("SAI ENTERPRISES", "", "", None, [], "", "", "India", "indiamart", shape="legacy"), "new")
    register("Sai Enterprises", "")
    truth["negatives"].append({"kind": "generic_name_chained_by_cityless_row", "lead_ids": [l["id"] for l in sai],
                               "wildcard": sai_blank["id"],
                               "note": "four different 'Sai Enterprises' in four cities; one more row has no city"})
    register("Verma Exports", "Delhi")
    register("Gupta Handicrafts", "Delhi")
    rv = life(build("Verma Exports", "Rohit Verma", "Owner", None, ["+91 11 4000 9001"], "", "Delhi", "", "justdial"), "new")
    sg = life(build("Gupta Handicrafts", "Swati Gupta", "Founder", None, ["011-4000-9001"], "", "Delhi", "", "justdial"),
              "new")
    truth["negatives"].append({"kind": "shared_landline_named_people", "lead_ids": [rv["id"], sg["id"]],
                               "note": "two named people at different firms in one business centre (same landline)"})
    centre = []
    for name in ("Zenith Tours", "Bluebay Interiors", "Crest Accounting Services"):
        register(name, "Noida")
        centre.append(life(build(name, "", "", None, ["+91 120 400 9002"], "", "Noida", "", "google_maps",
                                 phone_type="business"), "new"))
    truth["negatives"].append({"kind": "shared_landline_company_rows", "lead_ids": [l["id"] for l in centre],
                               "known_false_positive": True,
                               "note": "three firms in one business centre, company rows only (no contact names)"})
    for name_a, name_b, city in (("Balaji Traders", "Balaji Transport", "Pune"),
                                 ("Krishna Motors", "Krishna Medicals", "Lucknow"),
                                 ("Sai Electricals", "Sai Electronics", "Nagpur")):
        register(name_a, city)
        register(name_b, city)
        pair = [life(build(n, "", "", None, [mobile(group=True)], "", city, "", "justdial"), "new") for n in (name_a, name_b)]
        truth["negatives"].append({"kind": "similar_name_same_city", "lead_ids": [l["id"] for l in pair],
                                   "note": f"{name_a} vs {name_b}"})
    register("Nova Tech", "Hyderabad", "novatech.example")
    nt = [life(build("Nova Tech", "Imran Khan", "HR", "imran@novatech.example", [], "https://novatech.example",
                     "Hyderabad", "", "apollo"), "new"),
          life(build("Nova Tech", "Zoya Shaikh", "HR", "zoya@nova-tech-labs.invalid", [], "https://nova-tech-labs.invalid",
                     "Hyderabad", "", "apollo"), "new")]
    truth["negatives"].append({"kind": "same_name_different_domains", "lead_ids": [l["id"] for l in nt],
                               "note": "two firms called Nova Tech with different websites"})


# ---------------------------------------------------------------- singletons
def singleton_company(city: str) -> tuple:
    for _ in range(500):
        sector = rng.choice(list(SECTORS))
        name = " ".join(p for p in (rng.choice(PREFIX), rng.choice(MIDDLE), rng.choice(SECTORS[sector])) if p)
        domain = name.lower().replace(" ", "")[:22] + rng.choice([".example", ".invalid"])
        if not clashes(name, city) and domain not in used_domains:
            register(name, city, domain)
            return name, sector, domain
    raise RuntimeError("could not find a unique company name")


def plant_singletons(count: int):
    stages, weights = zip(*STAGE_WEIGHTS.items())
    for n in range(count):
        city = rng.choice(CITIES)
        name, sector, domain = singleton_company(city)
        first, last = person()
        legacy = rng.random() < 0.4
        source = rng.choice(SOURCES)
        stage = rng.choices(stages, weights)[0]
        if source == "bmi_sheet:leads" and stage in ("customer", "call_done"):
            source = "bmi_sheet:demo"
        style = rng.random()
        email = (None if style < 0.12 else f"hr@{domain}" if style < 0.2 else gmail(first, last) if style < 0.27
                 else f"{first}.{last}@{domain}".lower())
        hiring = rng.random()
        open_roles = rng.choice([0, 0, 1, 2, 3, 5]) if hiring < 0.6 else rng.choice([8, 10, 12, 15, 25, 40])
        job_age = rng.choice([0, 3, 7, 14]) if hiring < 0.5 else rng.choice([21, 30, 45, 60, 90])
        size = rng.choice([8, 15, 25, 60, 120, 250, 600, 1500, 4000])
        country = rng.choice(["IN", "India", "india", "", "IND"]) if legacy else rng.choice(["IN", "", "India"])
        if n % 50 == 7:
            country = rng.choice(["US", "AE", "United Kingdom"])
        title = rng.choice(TITLES_RAW)
        lead = build(name if not legacy else rng.choice([name.upper() + " PVT. LTD.", name + " Pvt Ltd", "M/S " + name.upper()]),
                     f"{first} {last}" if not legacy or rng.random() < 0.5 else f"{rng.choice(['MR.', 'MS.', 'DR.', 'SHRI', 'SMT.'])} {first.upper()} {last.upper()}",
                     title, email, [] if rng.random() < 0.25 else [mobile()],
                     "" if rng.random() < 0.15 else f"https://{'www.' if legacy else ''}{domain}",
                     LEGACY_CITY.get(city, city) if legacy else city, country, source,
                     shape="legacy" if legacy else "smart", sector=sector if rng.random() < 0.8 else "", size=size,
                     open_roles=open_roles, job_age=job_age, reposts=rng.choice([0, 0, 0, 1, 2]),
                     days=rng.choice([rng.randint(3, 60), rng.randint(3, 60), rng.randint(95, 200)]))
        if not lead["contact_name"].strip():
            lead["contact_name"] = ""
        life(lead, stage)


def plant_problems():
    """Sending problems, bounced/failed addresses, invalid stored emails and foreign numbers for the report and Today."""
    contacted = [l for l in store["leads"] if l["stage"] == "contacted" and l.get("sequence")]
    for lead in contacted[:3]:                                   # three failed sends each, sequence stopped
        for attempt in range(3):
            store["outbox"].append({"lead_id": lead["id"], "to": lead["email"], "step": "day 7", "status": "failed",
                                    "error": "550 mailbox unavailable", "subject": "Screening for your open roles",
                                    "sent_at": iso(NOW - timedelta(days=3 - attempt)),
                                    "sent_at_ist_date": (NOW - timedelta(days=3 - attempt)).date().isoformat()})
        lead["sequence"]["stopped"] = "send_failed"
        lead["sequence"]["failed_attempts"] = 3
    contacted[3]["sequence"]["in_flight"] = {"day": 7, "at": iso(NOW - timedelta(hours=5))}
    contacted[4]["sequence"]["blocked"] = "a detail needed by the email is missing"
    store["outbox"].append({"lead_id": contacted[5]["id"], "to": contacted[5]["email"], "step": "day 7",
                            "status": "provider_error", "error": "provider down", "retryable": True,
                            "sent_at": iso(NOW - timedelta(days=1)), "sent_at_ist_date": (NOW - timedelta(days=1)).date().isoformat()})
    truth["planted"]["failed_send_leads"] = [l["id"] for l in contacted[:3]]
    truth["planted"]["in_flight_lead"] = contacted[3]["id"]
    truth["planted"]["blocked_lead"] = contacted[4]["id"]
    news = [l for l in store["leads"] if l["stage"] == "new" and l["source"] in ("manual", "naukri") and l.get("email")
            and l["id"] not in {i for g in truth["groups"] for i in g["lead_ids"]}]
    bad = ["sales@kaveri-textiles", "hr.@shivamtex.example", "ravi kumar@@ojasprint.example"]
    for lead, value in zip(news, bad):
        lead["email"] = value
    truth["planted"]["invalid_email_leads"] = [l["id"] for l in news[:3]]


def plant_tasks():
    by_stage = lambda s: [l for l in store["leads"] if l["stage"] == s]
    interested = by_stage("interested") + by_stage("whatsapp_active")
    for i, lead in enumerate(interested):
        due = NOW - timedelta(days=1) if i % 3 == 0 else NOW + timedelta(hours=4)
        add_task(lead["id"], "call", "Call them", due, "high", "done" if i % 4 == 3 else "open",
                 "Said yes by email. Call through PD only, before 18:55.")
    for lead in by_stage("engaged"):
        add_task(lead["id"], "question", "Answer a question", NOW - timedelta(hours=1), text="what does it cost?")
    unsub = by_stage("unsubscribed")
    add_task(unsub[0]["id"], "erasure_request", "Erasure request", NOW + timedelta(hours=20), "high",
             text="Please delete all my data")
    add_task(unsub[1]["id"], "review", "Check a reply", NOW - timedelta(hours=5), "high", text="Yes please actually")
    disq = by_stage("disqualified")
    add_task(disq[0]["id"], "call", "Call them", NOW - timedelta(hours=2), "high",
             text="Said yes by email. Call through PD only, before 18:55.")
    add_task(disq[1]["id"], "review", "Check a reply", NOW - timedelta(hours=3), "high", text="Actually, yes, send it")
    add_task(None, "unmatched_reply", "Match a reply to a lead", NOW - timedelta(hours=6), text="from unknown@x.example")
    truth["planted"]["erasure_task_lead"] = unsub[0]["id"]
    truth["planted"]["unsub_review_lead"] = unsub[1]["id"]
    truth["planted"]["disqualified_call_task_lead"] = disq[0]["id"]
    truth["planted"]["disqualified_review_lead"] = disq[1]["id"]


_BUILT = []


def generate() -> tuple:
    """(store collections, planted truth). Built once per process; the generator is deterministic (fixed seed)."""
    if not _BUILT:
        plant_same_email()
        plant_gmail_alias()
        plant_same_phone()
        plant_same_domain_people()
        plant_company_only()
        plant_same_person_two_addresses()
        plant_fuzzy_names()
        plant_hard_positives()
        plant_negatives()
        plant_singletons(TOTAL - len(store["leads"]))
        plant_problems()
        plant_tasks()
        store["suppressed"] = sorted(set(store["suppressed"]) | {"bounced.qa001@gmail.com"})
        assert len(store["leads"]) == TOTAL, len(store["leads"])
        assert len({l["id"] for l in store["leads"]}) == TOTAL
        _BUILT.append((store, truth))
    return json.loads(json.dumps(_BUILT[0][0])), json.loads(json.dumps(_BUILT[0][1]))


def main():
    built, planted = generate()
    (HERE / "store_500.json").write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (HERE / "truth.json").write_text(json.dumps(planted, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(built['leads'])} leads, {len(planted['groups'])} planted groups, {len(planted['negatives'])} negatives")


if __name__ == "__main__":
    main()
