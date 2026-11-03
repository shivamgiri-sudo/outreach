"""Builds the LinkedIn-tool export fixtures in this folder (run: python make_fixtures.py). Deterministic.

Every person, company, number and address is made up: domains end in .example, phones are 90000 1xxxx, LinkedIn slugs
carry a '-qa' marker. One pool of 90 HR / founder contacts at 30 employers is shared by all tools, so the same person
shows up in several exports (with each tool's own spelling) and cross-tool dedupe can be checked against pool.json.
"""
import csv
import io
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
R = random.Random(20261103)

FIRST = ["Ravi", "Priya", "Gurpreet", "Sunita", "Pinky", "Bablu", "Sonu", "Neha", "Amit", "Kavya", "Rahul", "Anjali",
         "Mohd. Irfan", "Lakshmi", "Chintu", "Deepika", "Harpreet", "Raju", "Sneha", "Vikram", "Pooja", "Arjun", "Meenakshi",
         "Imran", "Divya", "Karthik", "Nandini", "Rohit", "Simran", "Ganesh"]
LAST = ["Kumar", "Sharma", "Singh", "Devi", "Kumari", "Yadav", "Gupta", "Verma", "Reddy", "Iyer", "Mehta", "Patel",
        "Khan", "Nair", "Chaudhary", "Das", "Kaur", "Bhai", "Joshi", "Rao"]
COMPANIES = [  # (display, legal as the tool shows it, domain stem, sector, city, state)
    ("Acme Retail", "ACME RETAIL PVT. LTD.", "acmeretail", "Retail", "Bengaluru", "Karnataka"),
    ("Punjab Agro", "Punjab Agro Private Limited", "punjabagro", "Farming", "Ludhiana", "Punjab"),
    ("Bharat Logistics", "Bharat Logistics Pvt Ltd", "bharatlogistics", "Logistics", "Pune", "Maharashtra"),
    ("Deccan Pharma", "DECCAN PHARMA LIMITED", "deccanpharma", "Pharmaceuticals", "Hyderabad", "Telangana"),
    ("Zenith Infotech", "Zenith Infotech LLP", "zenithinfotech", "IT Services", "Noida", "Uttar Pradesh"),
    ("Verma Foods", "Verma Foods (OPC) Pvt. Ltd.", "vermafoods", "Food Production", "Mumbai", "Maharashtra"),
    ("Kaveri Textiles", "KAVERI TEXTILES PVT LTD", "kaveritextiles", "Textiles", "Coimbatore", "Tamil Nadu"),
    ("Sahyadri Hospitals", "Sahyadri Hospitals Pvt. Ltd.", "sahyadrihospitals", "Hospitals and Health Care", "Pune",
     "Maharashtra"),
    ("Ganga Motors", "Ganga Motors Private Limited", "gangamotors", "Automotive", "Lucknow", "Uttar Pradesh"),
    ("Thar Solar", "Thar Solar Pvt Ltd", "tharsolar", "Renewable Energy", "Jaipur", "Rajasthan"),
    ("Konkan Seafoods", "KONKAN SEAFOODS PVT. LTD.", "konkanseafoods", "Food Production", "Ratnagiri", "Maharashtra"),
    ("Malabar Spices", "Malabar Spices Ltd", "malabarspices", "Food and Beverage", "Kochi", "Kerala"),
    ("Indus Fintech", "Indus Fintech Pvt. Ltd.", "indusfintech", "Financial Services", "Gurugram", "Haryana"),
    ("Brahmaputra Tea", "Brahmaputra Tea Company Limited", "brahmaputratea", "Farming", "Guwahati", "Assam"),
    ("Narmada Cement", "NARMADA CEMENT LIMITED", "narmadacement", "Building Materials", "Ahmedabad", "Gujarat"),
    ("Chambal Fertilisers QA", "Chambal Fertilisers QA Pvt Ltd", "chambalqa", "Chemicals", "Kota", "Rajasthan"),
    ("Hooghly Jute", "Hooghly Jute Mills Pvt. Ltd.", "hooghlyjute", "Textiles", "Kolkata", "West Bengal"),
    ("Vindhya Staffing", "Vindhya Staffing Solutions Pvt Ltd", "vindhyastaffing", "Staffing and Recruiting", "Indore",
     "Madhya Pradesh"),
    ("Nilgiri Dairy", "Nilgiri Dairy Farm Pvt. Ltd.", "nilgiridairy", "Dairy", "Chennai", "Tamil Nadu"),
    ("Sutlej Steel", "SUTLEJ STEEL PVT LTD", "sutlejsteel", "Mining and Metals", "Chandigarh", "Chandigarh"),
    ("Godavari BPO", "Godavari BPO Services Pvt. Ltd.", "godavaribpo", "Outsourcing", "Visakhapatnam", "Andhra Pradesh"),
    ("Aravalli Realty", "Aravalli Realty Pvt Ltd", "aravallirealty", "Real Estate", "Gurugram", "Haryana"),
    ("Tapti Chemicals", "Tapti Chemicals Limited", "taptichemicals", "Chemicals", "Surat", "Gujarat"),
    ("Mahanadi Power", "Mahanadi Power Pvt. Ltd.", "mahanadipower", "Utilities", "Bhubaneswar", "Odisha"),
    ("Yamuna Apparel", "YAMUNA APPAREL PRIVATE LIMITED", "yamunaapparel", "Apparel and Fashion", "Delhi", "Delhi"),
    ("Kosi Edtech", "Kosi Edtech Pvt Ltd", "kosiedtech", "E-Learning", "Patna", "Bihar"),
    ("Pench Hotels", "Pench Hotels & Resorts Pvt. Ltd.", "penchhotels", "Hospitality", "Nagpur", "Maharashtra"),
    ("Sabarmati Ceramics", "Sabarmati Ceramics Pvt Ltd", "sabarmaticeramics", "Building Materials", "Ahmedabad",
     "Gujarat"),
    ("Cauvery Electronics", "Cauvery Electronics Pvt. Ltd.", "cauveryelectronics", "Electronics", "Mysuru",
     "Karnataka"),
    ("Tungabhadra Agritech", "Tungabhadra Agritech LLP", "tungabhadra", "Farming", "Hubballi", "Karnataka"),
]
TITLES = ["HR Manager", "head - human resources", "Talent Acquisition Lead", "Founder & CEO", "Sr. HR Executive",
          "HR Business Partner", "Director", "CHRO", "Recruitment Manager", "HR Generalist", "VP - Human Resources"]
LOCATION_STYLES = ["{city}, {state}, India", "Greater {city} Area", "{city}, {state}", "{city}", "India"]
METRO = {"Mumbai": "Mumbai Metropolitan Region", "Delhi": "Delhi, India", "Bengaluru": "Greater Bengaluru Area"}


def build_pool():
    people = []
    used = set()
    for i in range(90):
        co = COMPANIES[i % len(COMPANIES)]
        while True:
            first, last = R.choice(FIRST), R.choice(LAST)
            if (first, last) not in used:
                used.add((first, last))
                break
        slug = f"{first.lower().replace('.', '').replace(' ', '-')}-{last.lower()}-qa{i:03d}"
        people.append({
            "pid": i, "first": first, "last": last, "name": f"{first} {last}", "slug": slug,
            "vmid": f"ACwAAQA{i:05d}xQa", "company": co[0], "legal": co[1], "domain": f"{co[2]}.example",
            "company_li_id": 70000 + COMPANIES.index(co), "company_li_slug": f"{co[2]}-qa",
            "sector": co[3], "city": co[4], "state": co[5], "title": TITLES[i % len(TITLES)],
            "email": f"{first.lower().split()[-1].strip('.')}.{last.lower()}@{co[2]}.example",
            "phone": f"90000{10000 + i:05d}",
        })
    return people


def messy_name(p, style):
    if style == "caps":
        return p["first"].upper(), p["last"].upper()
    if style == "lower":
        return p["first"].lower(), p["last"].lower()
    return p["first"], p["last"]


def phone_variant(p, k):
    n = p["phone"]
    return [f"+91 {n[:5]} {n[5:]}", f"0{n}", f"{n[:5]}-{n[5:]}", f"+91-{n}", f"91{n}"][k % 5]


def location(p, k):
    if p["city"] in METRO and k % 3 == 0:
        return METRO[p["city"]]
    return LOCATION_STYLES[k % len(LOCATION_STYLES)].format(city=p["city"], state=p["state"])


def write(name, header, rows, delimiter=",", bom=False):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    w.writerow(header)
    for row in rows:
        w.writerow([row.get(h, "") for h in header])
    (HERE / name).write_text(("﻿" if bom else "") + buf.getvalue(), encoding="utf-8", newline="")


def pick(people, n, offset):
    return [people[(offset + 7 * j) % len(people)] for j in range(n)]


def main():
    people = build_pool()
    files = {}

    # 1. PhantomBuster "Sales Navigator Search Export": camelCase, Sales Nav lead URL in profileUrl, vmid-based
    #    linkedInProfileUrl, slug in defaultProfileUrl, previous job in pastExperience* columns.
    header = ["profileUrl", "fullName", "firstName", "lastName", "companyName", "title", "companyId", "companyUrl",
              "regularCompanyUrl", "summary", "titleDescription", "industry", "companyLocation", "location",
              "durationInRole", "durationInCompany", "pastExperienceCompanyName", "pastExperienceCompanyUrl",
              "pastExperienceCompanyTitle", "pastExperienceDate", "pastExperienceDuration", "connectionDegree",
              "profileImageUrl", "sharedConnectionsCount", "name", "vmid", "linkedInProfileUrl", "isPremium",
              "isOpenLink", "query", "timestamp", "defaultProfileUrl", "searchAccountProfileId", "searchAccountProfileName"]
    rows = []
    for k, p in enumerate(people):
        first, last = messy_name(p, ["", "caps", "", "lower"][k % 4])
        past = COMPANIES[(k + 5) % len(COMPANIES)]
        current_missing = k % 15 == 4            # Sales Nav shows no current position: only the past one is known
        rows.append({
            "profileUrl": f"https://www.linkedin.com/sales/lead/{p['vmid']},NAME_SEARCH,q{k:03d}",
            "fullName": f"{first} {last}", "firstName": first, "lastName": last,
            "companyName": "" if current_missing else (p["legal"] if k % 2 else p["company"]),
            "title": "" if current_missing else p["title"], "companyId": "" if current_missing else p["company_li_id"],
            "companyUrl": "" if current_missing else f"https://www.linkedin.com/sales/company/{p['company_li_id']}",
            "regularCompanyUrl": "" if current_missing else f"https://www.linkedin.com/company/{p['company_li_id']}",
            "summary": "Building teams, hiring 50+ people this quarter.", "titleDescription": "",
            "industry": p["sector"], "companyLocation": f"{p['city']}, {p['state']}, India", "location": location(p, k),
            "durationInRole": f"{k % 5 + 1} years {k % 11} months in role", "durationInCompany": f"{k % 7 + 1} years",
            "pastExperienceCompanyName": past[1], "pastExperienceCompanyUrl": f"https://www.linkedin.com/sales/company/9{k}",
            "pastExperienceCompanyTitle": "HR Executive", "pastExperienceDate": "Jun 2016 – Mar 2020",
            "pastExperienceDuration": "3 yrs 10 mos", "connectionDegree": ["2nd", "3rd", "1st"][k % 3],
            "profileImageUrl": f"https://media.example/img/{k}.jpg", "sharedConnectionsCount": k % 9,
            "name": f"{first} {last}", "vmid": p["vmid"], "linkedInProfileUrl": f"https://www.linkedin.com/in/{p['vmid']}",
            "isPremium": "true" if k % 4 == 0 else "false", "isOpenLink": "false",
            "query": "https://www.linkedin.com/sales/search/people?query=(keywords%3AHR%20head)",
            "timestamp": f"2026-10-{20 + k % 8:02d}T0{k % 9}:15:00.000Z",
            "defaultProfileUrl": f"https://www.linkedin.com/in/{p['slug']}",
            "searchAccountProfileId": "", "searchAccountProfileName": "QA Seller",
        })
    rows.append(rows[3])                                         # PhantomBuster re-run appended the same lead twice
    write("phantombuster_salesnav_search.csv", header, rows)
    files["phantombuster_salesnav_search.csv"] = len(rows)

    # 2. Evaboot (Sales Navigator list export, cleaned + verified emails); Title Case headers, Excel-style dates.
    header = ["Sales Navigator Profile URL", "LinkedIn Profile URL", "First Name", "Last Name", "Job Title",
              "Profile Headline", "Company Name", "Company Domain", "Company Website", "Company LinkedIn URL",
              "Company Industry", "Company Employee Count", "Company Employee Count Range", "Location", "Company Location",
              "Email", "Email Status", "Years in Position", "Years in Company", "Connection Degree", "Is Premium",
              "Company Founded", "Company Specialties", "Exported On"]
    rows = []
    for k, p in enumerate(pick(people, 75, 1)):
        status = ["Valid", "Valid", "Catch-all", "Not found"][k % 4]
        rows.append({
            "Sales Navigator Profile URL": f"https://www.linkedin.com/sales/lead/{p['vmid']},NAME_SEARCH,e{k}",
            "LinkedIn Profile URL": f"https://www.linkedin.com/in/{p['slug']}/",
            "First Name": p["first"], "Last Name": p["last"], "Job Title": p["title"],
            "Profile Headline": f"{p['title']} at {p['legal']} | Hiring across India",
            "Company Name": p["company"], "Company Domain": p["domain"], "Company Website": f"https://www.{p['domain']}/",
            "Company LinkedIn URL": f"https://www.linkedin.com/company/{p['company_li_slug']}/",
            "Company Industry": p["sector"], "Company Employee Count": 50 + (k * 37) % 350,
            "Company Employee Count Range": "51-200", "Location": location(p, k + 1),
            "Company Location": f"{p['city']}, {p['state']}, India",
            "Email": "" if status == "Not found" else p["email"], "Email Status": status,
            "Years in Position": k % 6, "Years in Company": k % 9, "Connection Degree": "2nd", "Is Premium": "No",
            "Company Founded": 1990 + k % 30, "Company Specialties": "Manufacturing, Retail, Hiring",
            "Exported On": f"{k % 28 + 1:02d}/10/2026",
        })
    write("evaboot_salesnav_list.csv", header, rows)
    files["evaboot_salesnav_list.csv"] = len(rows)

    # 3. PhantomBuster "LinkedIn Profile Scraper": headline + current job, a second (previous) job as company2/jobTitle2.
    header = ["linkedinProfileUrl", "firstName", "lastName", "fullName", "headline", "description", "location",
              "company", "companyUrl", "jobTitle", "jobDescription", "jobLocation", "jobDateRange", "jobDuration",
              "company2", "companyUrl2", "jobTitle2", "jobDateRange2", "school", "schoolUrl", "schoolDegree", "mail",
              "phoneNumber", "website", "linkedinProfile", "vmid", "connectionDegree", "timestamp", "query", "error"]
    rows = []
    for k, p in enumerate(pick(people, 60, 2)):
        prev = COMPANIES[(k + 9) % len(COMPANIES)]
        no_current = k % 12 == 5
        rows.append({
            "linkedinProfileUrl": f"https://www.linkedin.com/in/{p['slug']}", "firstName": p["first"],
            "lastName": p["last"], "fullName": p["name"].upper() if k % 5 == 0 else p["name"],
            "headline": f"{p['title']} @ {p['company']} | People & Culture", "description": "Hiring for 3 cities.",
            "location": location(p, k + 2), "company": "" if no_current else p["company"],
            "companyUrl": "" if no_current else f"https://www.linkedin.com/company/{p['company_li_slug']}/",
            "jobTitle": "" if no_current else p["title"], "jobDescription": "", "jobLocation": p["city"],
            "jobDateRange": "Jan 2021 - Present", "jobDuration": "5 yrs", "company2": prev[0],
            "companyUrl2": f"https://www.linkedin.com/company/{prev[2]}-old/", "jobTitle2": "HR Trainee",
            "jobDateRange2": "2017 - 2020", "school": "Fictional Institute of Management",
            "schoolUrl": "https://www.linkedin.com/school/fictional-institute-qa/", "schoolDegree": "MBA",
            "mail": p["email"] if k % 3 == 0 else "", "phoneNumber": phone_variant(p, k) if k % 4 == 0 else "",
            "website": "", "linkedinProfile": f"https://www.linkedin.com/in/{p['slug']}", "vmid": p["vmid"],
            "connectionDegree": "1st", "timestamp": "2026-10-29T06:00:00.000Z",
            "query": f"https://www.linkedin.com/in/{p['slug']}", "error": "",
        })
    rows.append({"linkedinProfileUrl": "", "query": "https://www.linkedin.com/in/deleted-profile-qa",
                 "error": "Can't open profile", "timestamp": "2026-10-29T06:10:00.000Z"})
    write("phantombuster_profile_scraper.csv", header, rows)
    files["phantombuster_profile_scraper.csv"] = len(rows)

    # 4. Waalaxy prospect-list export, re-saved by Excel in a semicolon locale with a BOM. "Occupation" is the headline.
    header = ["First Name", "Last Name", "Occupation", "Company", "Company URL", "Location", "Email",
              "Dropcontact Email", "Phone Numbers", "Prospect List", "LinkedIn URL", "Sales Navigator URL", "Premium",
              "Job Seeker", "Profile Status", "Message Sent", "Message Replied", "Email Sent", "Email Replied",
              "Connected At", "First Message At", "Tags"]
    rows = []
    for k, p in enumerate(pick(people, 55, 3)):
        first, last = messy_name(p, ["caps", "", "lower", ""][k % 4])
        rows.append({
            "First Name": first, "Last Name": last,
            "Occupation": f"{p['title']} at {p['legal']} | Ex-TCS | Hiring 100+" if k % 2 else p["title"],
            "Company": p["company"], "Company URL": f"https://www.linkedin.com/company/{p['company_li_slug']}",
            "Location": location(p, k + 3), "Email": "", "Dropcontact Email": p["email"] if k % 3 else "",
            "Phone Numbers": phone_variant(p, k) if k % 5 == 0 else "", "Prospect List": "HR heads Nov",
            "LinkedIn URL": f"https://in.linkedin.com/in/{p['slug']}",
            "Sales Navigator URL": f"https://www.linkedin.com/sales/lead/{p['vmid']}", "Premium": "false",
            "Job Seeker": "false", "Profile Status": ["connected", "not_connected", "pending"][k % 3],
            "Message Sent": "true" if k % 2 else "false", "Message Replied": "false", "Email Sent": "false",
            "Email Replied": "false", "Connected At": f"{k % 28 + 1:02d}/10/2026 09:15",
            "First Message At": "", "Tags": "hot;india" if k % 6 == 0 else "",
        })
    rows.append({})                                               # blank line Excel leaves at the end
    rows.append({"First Name": "Xss", "Last Name": "Probe", "Occupation": "HR", "Company": "Probe Labs",
                 "Company URL": "javascript:alert(1)", "Location": "Noida, Uttar Pradesh, India",
                 "LinkedIn URL": "javascript:alert(document.cookie)//www.linkedin.com/in/xss-probe-qa",
                 "Prospect List": "HR heads Nov"})
    write("waalaxy_prospects_semicolon.csv", header, rows, delimiter=";", bom=True)
    files["waalaxy_prospects_semicolon.csv"] = len(rows)

    # 5. Dux-Soup visit export: "Profile" is the LinkedIn URL, "Middle Name" column, CompanyProfile / CompanyWebsite.
    header = ["VisitTime", "Profile", "SalesProfile", "Picture", "Degree", "First Name", "Middle Name", "Last Name",
              "Summary", "Title", "Company", "CompanyProfile", "CompanyWebsite", "PersonalWebsite", "Location",
              "Industry", "Email", "Phone", "Twitter", "Tags", "Notes"]
    rows = []
    for k, p in enumerate(pick(people, 50, 4)):
        rows.append({
            "VisitTime": f"2026-10-{k % 28 + 1:02d} 14:{k % 60:02d}:11", "Profile": f"https://www.linkedin.com/in/{p['slug']}",
            "SalesProfile": f"https://www.linkedin.com/sales/people/{p['vmid']},name", "Picture": "",
            "Degree": ["2nd", "3rd"][k % 2], "First Name": p["first"], "Middle Name": "Kumar" if k % 3 == 0 else "",
            "Last Name": p["last"], "Summary": "", "Title": p["title"], "Company": p["legal"],
            "CompanyProfile": f"https://www.linkedin.com/company/{p['company_li_id']}",
            "CompanyWebsite": f"www.{p['domain']}" if k % 2 else "", "PersonalWebsite": "",
            "Location": location(p, k + 4), "Industry": p["sector"], "Email": p["email"] if k % 4 == 1 else "",
            "Phone": phone_variant(p, k) if k % 4 == 2 else "", "Twitter": "", "Tags": "dux-hr", "Notes": "",
        })
    rows.append({"VisitTime": "2026-10-30 10:00:00", "Profile": "javascript:void(fetch('//evil.invalid'))",
                 "First Name": "Js", "Last Name": "Link", "Company": "Script Kiddie Pvt Ltd",
                 "CompanyProfile": "JAVASCRIPT:alert(1)", "CompanyWebsite": "javascript:alert(2)",
                 "Location": "Pune, Maharashtra, India"})
    write("dux_soup_visits.csv", header, rows)
    files["dux_soup_visits.csv"] = len(rows)

    # 6. Kaspr export: work + direct email, numbered phones, LinkedIn as "LinkedIn".
    header = ["First name", "Last name", "Title", "Company", "LinkedIn", "Work email", "Direct email", "Phone 1",
              "Phone 2", "Location", "Company website", "List", "Exported at"]
    rows = []
    for k, p in enumerate(pick(people, 50, 5)):
        rows.append({
            "First name": p["first"], "Last name": p["last"], "Title": p["title"], "Company": p["legal"],
            "LinkedIn": f"linkedin.com/in/{p['slug']}", "Work email": p["email"] if k % 2 == 0 else "",
            "Direct email": f"{p['first'].lower().split()[-1].strip('.')}{p['pid']}@gmail.com" if k % 5 == 0 else "",
            "Phone 1": phone_variant(p, k), "Phone 2": "", "Location": location(p, k + 5),
            "Company website": p["domain"], "List": "Kaspr HR India", "Exported at": "2026-10-31 18:00",
        })
    write("kaspr_contacts.csv", header, rows)
    files["kaspr_contacts.csv"] = len(rows)

    # 7. Lemlist campaign lead export (LinkedIn-sourced leads): camelCase.
    header = ["email", "firstName", "lastName", "companyName", "companyDomain", "jobTitle", "linkedinUrl", "phone",
              "icebreaker", "picture", "location", "lastState"]
    rows = []
    for k, p in enumerate(pick(people, 40, 6)):
        rows.append({
            "email": p["email"], "firstName": p["first"], "lastName": p["last"], "companyName": p["company"],
            "companyDomain": p["domain"], "jobTitle": p["title"], "linkedinUrl": f"http://linkedin.com/in/{p['slug']}",
            "phone": phone_variant(p, k) if k % 3 == 0 else "", "icebreaker": "Saw you are hiring.", "picture": "",
            "location": location(p, k + 6), "lastState": "emailsSent",
        })
    write("lemlist_leads.csv", header, rows)
    files["lemlist_leads.csv"] = len(rows)

    # 8. LinkedIn Recruiter "Save to CSV" style (project export): Headline + Current Title + Current Company.
    header = ["First Name", "Last Name", "Headline", "Location", "Current Title", "Current Company", "Email Address",
              "Phone Number", "Profile URL", "Active Project", "Notes", "Source"]
    rows = []
    for k, p in enumerate(pick(people, 50, 8)):
        rows.append({
            "First Name": p["first"], "Last Name": p["last"], "Headline": f"{p['title']} at {p['company']}",
            "Location": location(p, k), "Current Title": p["title"], "Current Company": p["legal"],
            "Email Address": p["email"] if k % 3 == 0 else "", "Phone Number": phone_variant(p, k) if k % 4 == 0 else "",
            "Profile URL": f"https://www.linkedin.com/in/{p['slug']}", "Active Project": "HR leaders Q4",
            "Notes": "Met at HR summit" if k % 10 == 0 else "", "Source": "Recruiter search",
        })
    write("linkedin_recruiter_project.csv", header, rows)
    files["linkedin_recruiter_project.csv"] = len(rows)

    (HERE / "pool.json").write_text(json.dumps({"files": files, "people": people}, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8")


if __name__ == "__main__":
    main()
