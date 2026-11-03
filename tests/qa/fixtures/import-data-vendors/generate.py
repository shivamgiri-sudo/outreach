"""Deterministic generator for the CSVs in this folder: `python generate.py`. Everything is fictional: made-up
people and companies, *.example domains, '.qa<n>' gmail addresses that are never mailed, fake 90000/90001 numbers.
Column headers follow each vendor's export as described in docs/qa/import-data-vendors.md."""
import csv, io, random, sys
from pathlib import Path
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
OUT.mkdir(parents=True, exist_ok=True)
rnd = random.Random(20261103)
FIRST = ["Ravi", "Anita", "Sunil", "Pooja", "Imran", "Neha", "Arjun", "Kavya", "Rohit", "Sneha", "Vikram", "Priya",
         "Manpreet", "Farah", "Deepak", "Lakshmi", "Gaurav", "Swati", "Harish", "Meera", "Tushar", "Aisha", "Kiran", "Nitin"]
LAST = ["Kumar", "Rao", "Sharma", "Iyer", "Khan", "Verma", "Nair", "Gupta", "Patil", "Singh", "Reddy", "Joshi", "Bhatia",
        "Menon", "Das", "Chawla", "Kulkarni", "Shetty"]
TITLES = ["HR Manager", "Talent Acquisition Lead", "Head - Human Resources", "HR Business Partner", "Recruitment Manager",
          "VP People", "Founder & CEO", "HR Executive", "Sr. Manager - TA", "Chief People Officer"]
CITIES = [("Bengaluru", "Karnataka"), ("Pune", "Maharashtra"), ("Gurugram", "Haryana"), ("Noida", "Uttar Pradesh"),
          ("Hyderabad", "Telangana"), ("Chennai", "Tamil Nadu"), ("Mumbai", "Maharashtra"), ("Ahmedabad", "Gujarat")]
INDUSTRY = ["Staffing & Recruiting", "Information Technology & Services", "Retail", "BPO", "Logistics", "Hospitality"]
SIZES = ["51-200", "201-500", "11-50", "501-1000"]
WORDS = ["Nimbus", "Sarovar", "Kesari", "Trident", "Pragati", "Udaan", "Vistara", "Saffron", "Lotus", "Indigo", "Banyan",
         "Chakra", "Garuda", "Tulsi", "Monsoon", "Himalaya", "Kaveri", "Ganga", "Sahyadri", "Aravali", "Nilgiri", "Deccan",
         "Konkan", "Malabar", "Narmada", "Shivalik", "Thar", "Vindhya", "Yamuna", "Zanskar"]
SUFFIX = ["Pvt. Ltd.", "Private Limited", "Technologies Pvt Ltd", "Solutions LLP", "Retail Pvt. Ltd.", "Services Ltd"]

def person(i):
    w = WORDS[i % len(WORDS)]
    first, last = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
    city, state = CITIES[i % len(CITIES)]
    dom = f"{w.lower()}{'-hr' if i % 4 == 0 else ''}.example"
    return {"i": i, "first": first, "last": last, "company": f"{w} {SUFFIX[i % len(SUFFIX)]}", "domain": dom,
            "title": TITLES[i % len(TITLES)], "city": city, "state": state, "industry": INDUSTRY[i % len(INDUSTRY)],
            "size": SIZES[i % len(SIZES)], "email": f"{first.lower()}.{last.lower()}@{dom}",
            "mobile": f"90000{10000 + i:05d}", "direct": f"90001{10000 + i:05d}", "hq": f"080 4100 {1000 + i:04d}",
            "li": f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-qa{i:03d}"}

POOL = [person(i) for i in range(60)]

def phone_variant(n, k):
    return [f"+91 {n[:5]} {n[5:]}", f"0{n}", n, f"91-{n[:5]}-{n[5:]}", f"+91{n}"][k % 5]

def write(name, headers, rows, delimiter=",", bom=False, crlf=False):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n" if crlf else "\n")
    w.writerow(headers); w.writerows(rows)
    (OUT / name).write_bytes((("﻿" if bom else "") + buf.getvalue()).encode("utf-8"))

# ---- Lusha: people 0-29; P0 title 'HR Manager'. Some ALL CAPS; blank row; duplicate row (P5 twice)
H = ["First Name", "Last Name", "Job Title", "Company Name", "Company Domain", "Email 1", "Email 1 Type", "Email 2",
     "Email 2 Type", "Phone 1", "Phone 1 Type", "Phone 2", "Phone 2 Type", "LinkedIn URL", "Country", "City",
     "Company Size", "Company Industry"]
rows = []
for p in POOL[:30]:
    first, last = (p["first"].upper(), p["last"].upper()) if p["i"] % 6 == 1 else (p["first"], p["last"])
    rows.append([first, last, p["title"], p["company"], p["domain"], p["email"], "Work",
                 f"{p['first'].lower()}.qa{p['i']}@gmail.com" if p["i"] % 5 == 0 else "", "Personal" if p["i"] % 5 == 0 else "",
                 phone_variant(p["direct"], p["i"]), "Direct", phone_variant(p["mobile"], p["i"] + 1) if p["i"] % 3 else "",
                 "Mobile" if p["i"] % 3 else "", p["li"], "India", p["city"], p["size"], p["industry"]])
rows.insert(10, [""] * len(H))
rows.append(rows[5])
write("lusha_prospecting.csv", H, rows)

# ---- ZoomInfo: people 20-49 (overlap 20-29 with Lusha, different work email alias, same direct phone). BOM + CRLF.
H = ["ZoomInfo Contact ID", "Last Name", "First Name", "Middle Name", "Salutation", "Job Title", "Management Level",
     "Direct Phone Number", "Email Address", "Email Domain", "Mobile phone", "Contact Accuracy Score",
     "LinkedIn Contact Profile URL", "Person City", "Person State", "Country", "ZoomInfo Company ID", "Company Name",
     "Website", "Company HQ Phone", "Employees", "Employee Range", "Primary Industry", "Company City", "Company State",
     "Company Country"]
rows = []
for p in POOL[20:50]:
    email = f"{p['first'][0].lower()}{p['last'].lower()}@{p['domain']}" if p["i"] < 30 else p["email"]
    title = "VP - Human Resources" if p["i"] == 20 else p["title"]
    rows.append([f"{1200000000 + p['i']}", p["last"], p["first"], "K" if p["i"] % 2 else "", "Mr." if p["i"] % 2 else "Ms.",
                 title, "Manager", phone_variant(p["direct"], p["i"] + 2), email, p["domain"], "", f"{80 + p['i'] % 20}",
                 p["li"], p["city"], p["state"], "India", f"{3400000 + p['i']}", p["company"].upper() if p["i"] % 7 == 0 else p["company"],
                 f"www.{p['domain']}", p["hq"], "350", "250 - 499", p["industry"], "San Jose" if p["i"] == 45 else p["city"],
                 "California" if p["i"] == 45 else p["state"], "United States" if p["i"] == 45 else "India"])
write("zoominfo_contacts.csv", H, rows, bom=True, crlf=True)

# ---- Seamless.AI: semicolon CSV (Excel, EU locale). People 40-59 + 0-4. Email 1 Validation, Contact/Company phones.
H = ["First Name", "Last Name", "Title", "Company", "Email 1", "Email 1 Validation", "Email 1 Total AI", "Personal Email",
     "Contact Phone 1", "Contact Phone 1 Total AI", "Company Phone 1", "Contact LI Profile URL", "Company LI Profile Url",
     "Website", "Contact City", "Contact State", "Contact Country", "Industry", "Company Staff Count Range"]
rows = []
for p in POOL[40:60] + POOL[:5]:
    val = "Invalid" if p["i"] in (41, 52) else "Catch-all" if p["i"] % 4 == 3 else "Valid"
    rows.append([p["first"], p["last"], p["title"], p["company"], p["email"].upper() if p["i"] < 5 else p["email"], val,
                 f"{70 + p['i'] % 30}%", "", phone_variant(p["mobile"], p["i"]), "High", p["hq"], p["li"],
                 f"https://www.linkedin.com/company/{p['domain'].split('.')[0]}", f"https://{p['domain']}/",
                 p["city"], p["state"], "IN", p["industry"], f"{p['size']} employees"])
write("seamless_ai.csv", H, rows, delimiter=";")

# ---- RocketReach: Name (full), Email Grade, Location; people 30-44. P31 has only a personal gmail.
H = ["Name", "Title", "Company", "Company Domain", "Email", "Email Grade", "Personal Email", "Work Phone", "Mobile Phone",
     "LinkedIn", "Location", "Country Code", "Company Industry", "Company Size"]
rows = []
for p in POOL[30:45]:
    rows.append([f"{p['first']} {p['last']}", p["title"], p["company"], p["domain"], p["email"] if p["i"] != 31 else "",
                 "F" if p["i"] == 33 else "A" if p["i"] % 2 else "B", f"{p['first'].lower()}.{p['last'].lower()}qa{p['i']}@gmail.com",
                 p["hq"], phone_variant(p["mobile"], p["i"]), p["li"], f"{p['city']}, {p['state']}, India", "IN",
                 p["industry"], p["size"]])
write("rocketreach.csv", H, rows)

# ---- Hunter domain search: one domain (P10's company), several people incl. a generic inbox + invalid one.
p = POOL[10]
H = ["Email address", "Type", "Confidence score", "Number of sources", "Pattern", "First name", "Last name", "Department",
     "Position", "Twitter handle", "LinkedIn URL", "Phone number", "Verification status", "Last verification date",
     "Domain name", "Organization"]
rows = [[p["email"], "personal", "97", "4", "{first}.{last}", p["first"], p["last"], "hr", p["title"], "", p["li"], "",
         "valid", "2026-10-28", p["domain"], p["company"]],
        [f"hr@{p['domain']}", "generic", "92", "11", "", "", "", "", "", "", "", "", "accept_all", "2026-10-28", p["domain"], p["company"]],
        [f"careers@{p['domain']}", "generic", "88", "6", "", "", "", "", "", "", "", "", "valid", "2026-10-27", p["domain"], p["company"]],
        [f"tushar.menon@{p['domain']}", "personal", "64", "1", "{first}.{last}", "Tushar", "Menon", "management",
         "Director", "", "", "", "invalid", "2026-10-26", p["domain"], p["company"]],
        [f"swati.joshi@{p['domain']}", "personal", "81", "2", "{first}.{last}", "Swati", "Joshi", "hr", "HR Executive", "",
         "", "+91 90002 20010", "valid", "2026-10-27", p["domain"], p["company"]]]
write("hunter_domain_search.csv", H, rows)

# ---- Hunter bulk email finder: input cols + results. People 50-59; one not found.
H = ["First name", "Last name", "Domain name", "Email address", "Confidence score", "Verification status"]
rows = [[q["first"], q["last"], q["domain"], "" if q["i"] == 55 else q["email"], "" if q["i"] == 55 else "91",
         "" if q["i"] == 55 else ("valid" if q["i"] % 3 else "accept_all")] for q in POOL[50:60]]
write("hunter_email_finder.csv", H, rows)

# ---- Snov.io: people 5-19, Email status valid / not valid / unknown; Social URL = LinkedIn.
H = ["First name", "Last name", "Full name", "Industry", "Country", "Location", "Position", "Company name", "Company URL",
     "Email", "Email status", "Social URL", "Phone"]
rows = [[q["first"], q["last"], f"{q['first']} {q['last']}", q["industry"], "India", f"{q['city']}, India", q["title"],
         q["company"], f"http://{q['domain']}", q["email"], "not valid" if q["i"] == 7 else "unknown" if q["i"] % 4 == 0 else "valid",
         q["li"], ""] for q in POOL[5:20]]
write("snov_prospects.csv", H, rows)

# ---- HubSpot contacts: people 0-14 + new; Unsubscribed from all email true for P3; Excel-ish dates.
H = ["Record ID", "First Name", "Last Name", "Email", "Phone Number", "Mobile Phone Number", "Job Title", "Company Name",
     "Website URL", "City", "State/Region", "Country/Region", "Lifecycle Stage", "Lead Status", "Contact owner",
     "Create Date", "Industry", "Number of Employees", "Unsubscribed from all email"]
rows = [[f"{51000 + q['i']}", q["first"], q["last"], q["email"], q["hq"], phone_variant(q["mobile"], q["i"] + 3), q["title"],
         q["company"], q["domain"], q["city"], q["state"], "India", "Lead", "Unqualified" if q["i"] == 9 else "New",
         "Sales Team", f"{3 + q['i'] % 20:02d}/10/2026 10:{q['i']:02d}", q["industry"], "120",
         "true" if q["i"] == 3 else "false"] for q in POOL[:15]]
write("hubspot_contacts.csv", H, rows)

# ---- Salesforce lead report: Company / Account, Email Opt Out 0/1, Do Not Call; people 12-24.
H = ["First Name", "Last Name", "Title", "Company / Account", "Email", "Phone", "Mobile", "City", "State/Province",
     "Country", "Lead Status", "Lead Source", "Industry", "No. of Employees", "Website", "Lead Owner", "Create Date",
     "Email Opt Out", "Do Not Call"]
rows = [[q["first"], q["last"], q["title"], q["company"], q["email"], q["hq"], phone_variant(q["mobile"], q["i"]), q["city"],
         q["state"], "India", "Open - Not Contacted", "Purchased List", q["industry"], "300", q["domain"], "QA Owner",
         f"{q['i'] % 28 + 1}/10/2026", "1" if q["i"] == 14 else "0", "1" if q["i"] == 16 else "0"] for q in POOL[12:25]]
write("salesforce_leads_report.csv", H, rows)

# ---- Zoho CRM leads: people 25-34, Email Opt Out true/false, Secondary Email (gmail with dots).
H = ["Record Id", "Lead Owner", "Company", "First Name", "Last Name", "Title", "Email", "Phone", "Mobile", "Website",
     "Lead Source", "Lead Status", "Industry", "No. of Employees", "Annual Revenue", "Email Opt Out", "City", "State",
     "Country", "Zip Code", "Secondary Email", "Created Time", "Description"]
rows = [[f"zcrm_48000000{q['i']:04d}", "QA Owner", q["company"], q["first"], q["last"], q["title"], q["email"], q["hq"],
         phone_variant(q["mobile"], q["i"]), f"https://www.{q['domain']}", "Cold Call", "Not Contacted", q["industry"], "75",
         "", "true" if q["i"] == 27 else "false", q["city"], q["state"], "India", "560001",
         f"{q['first'].lower()}.{q['last'].lower()}.qa.{q['i']}@gmail.com", f"2026-10-{q['i'] % 28 + 1:02d} 09:30:00",
         "Met at HR summit" if q["i"] == 26 else ""] for q in POOL[25:35]]
write("zoho_leads.csv", H, rows)

# ---- Pipedrive people export: people 35-44 + 2 extras without email.
H = ["Person - Name", "Person - Email - Work", "Person - Email - Other", "Person - Phone - Work", "Person - Phone - Mobile",
     "Person - Organization", "Person - Job title", "Person - Label", "Person - Owner", "Organization - Address"]
rows = [[f"{q['first']} {q['last']}", q["email"], "", q["hq"], phone_variant(q["mobile"], q["i"]), q["company"], q["title"],
         "Warm" if q["i"] % 2 else "Cold", "QA Owner", f"{q['i']} MG Road, {q['city']}, {q['state']} 4110{q['i']:02d}"]
        for q in POOL[35:45]]
rows.append(["Bhavesh Thakkar", "", "", "", "+91 90003 30001", "Kesari Udyog", "Proprietor", "", "QA Owner", "Ahmedabad"])
write("pipedrive_people.csv", H, rows)
print("ok")
