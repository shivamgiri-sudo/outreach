"""Regenerate the fictional fixtures in this folder: python make_fixtures.py <this folder>. Deterministic (seeded)."""
import csv, io, random, sys
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
R = random.Random(20261103)

STEMS = ["Shubh Labh", "Ganpati", "Saraswati", "Navrang", "Kesari", "Trimurti", "Annapurna", "Sai Kripa", "Jai Ambe",
         "Neelkanth", "Pragati", "Sunrise", "Dhanlaxmi", "Omkar", "Swastik", "Vardhman", "Maruti", "Shree Balaji",
         "Udaan", "Krishna Kunj", "Sahyadri", "Kaveri", "Tapti", "Narmada", "Indus Valley", "Ashoka", "Chanakya",
         "Mahavir", "Gulmohar", "Amaltas", "Parijat", "Tulsi", "Chandan", "Kesar", "Mogra", "Rangoli", "Toran",
         "Diya", "Kalash", "Mandala"]
KINDS = ["Logistics", "Retail", "BPO Services", "Facility Management", "Staffing", "Foods", "Pharma", "Textiles",
         "Hospitality", "Fintech"]
SUFFIX = [" Pvt. Ltd.", " Private Limited", " PVT LTD", " LLP", "", " (OPC) Pvt Ltd"]
CITY_SPELL = [("Bangalore", "080", "560"), ("Bengaluru", "080", "560"), ("Gurgaon", "0124", "122"),
              ("Gurugram", "0124", "122"), ("Delhi NCR", "011", "110"), ("New Delhi", "011", "110"),
              ("Mumbai", "022", "400"), ("Pune", "020", "411"), ("Hyderabad", "040", "500"), ("Chennai", "044", "600"),
              ("Noida", "0120", "201"), ("Kolkata", "033", "700")]
ROLES = ["Telecaller", "Customer Support Executive", "Field Sales Executive", "Delivery Associate", "Back Office Executive",
         "Store Manager", "Warehouse Picker", "Inside Sales Associate", "Collection Executive", "Security Guard"]
FIRST = ["Pinky", "Bablu", "Chintu", "Guddi", "Monu", "Sonu", "Raju", "Tinku", "Dolly", "Bunty", "Rinku", "Munni"]
LAST = ["Testwala", "Sampleji", "Fakeshwar", "Demo Singh", "Exampur", "Kalpanik"]

companies = []
for i, stem in enumerate(STEMS):
    kind = KINDS[i % len(KINDS)]
    city, std, pin = CITY_SPELL[i % len(CITY_SPELL)]
    companies.append({"base": f"{stem} {kind}", "suffix": SUFFIX[i % len(SUFFIX)], "city": city, "std": std,
                      "pin": pin, "slug": stem.lower().replace(" ", "") + kind.split()[0].lower(), "i": i})


def mobile(i, style):
    n = f"90000{10000 + i:05d}"
    return [n, f"+91 {n[:5]} {n[5:]}", f"0{n}", f"+91-{n}", f"91{n}", f"{n[:5]}-{n[5:]}"][style % 6]


def landline(c, style):
    sub = f"4{(c['i'] * 7919) % 10000000:07d}"[: 10 - len(c["std"]) + 1]
    std = c["std"]
    return [f"{std}-{sub}", f"({std}) {sub[:4]} {sub[4:]}", f"+91 {std[1:]} {sub}", f"{std} {sub}"][style % 4]


def write(name, header, rows, delimiter=",", bom=False, crlf=False):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n" if crlf else "\n")
    w.writerow(header)
    w.writerows(rows)
    (OUT / name).write_text(("﻿" if bom else "") + buf.getvalue(), encoding="utf-8", newline="")


def cname(c, upper=False):
    s = c["base"] + c["suffix"]
    return s.upper() if upper else s


# 1. Naukri, team copy-paste layout (detected as job_board): 120 rows, 40 companies x ~3 openings, dup rows
rows = []
for k in range(120):
    c = companies[k % 40]
    posted = ["3 days ago", "30+ days ago", "Just now", "1 week ago", "13-Oct-2026", "2026-10-20", "28/10/2026"][k % 7]
    rows.append([cname(c, upper=k % 5 == 0), ROLES[k % 10], f"{k % 4}-{k % 4 + 3} Yrs", "2-3.5 Lacs P.A.",
                 c["city"] if k % 3 else f"{c['city']}, Hyderabad", posted, str(1 + k % 12),
                 f"https://www.naukri.com/job-listings-{k:06d}", "calling, excel"])
rows.insert(30, [""] * 9)
rows.append(rows[5][:])
write("naukri_team_copy.csv", ["Company Name", "Job Title", "Experience", "Salary", "Location", "Posted On",
                               "No. of Openings", "Job Link", "Key Skills"], rows)

# 2. Naukri Apify-style camelCase: 60 rows
rows = []
for k in range(60):
    c = companies[(k * 3) % 40]
    rows.append([f"{261000000 + k}", ROLES[(k + 2) % 10], cname(c), "1-4 Yrs", "Not disclosed",
                 f"{c['city']}(Sector {k % 60}), Remote" if k % 4 == 0 else c["city"],
                 ["Today", "2 Days Ago", "30+ Days Ago", "5 Days Ago"][k % 4],
                 f"https://www.naukri.com/job-listings-x-{k}", "voice process", str(k % 6 or "")])
write("naukri_apify.csv", ["jobId", "title", "companyName", "experience", "salary", "location",
                           "footerPlaceholderLabel", "jdURL", "tagsAndSkills", "vacancy"], rows)

# 3. Indeed scraper (Apify style): 60 rows
rows = []
for k in range(60):
    c = companies[(k * 7) % 40]
    rows.append([ROLES[k % 10], cname(c, upper=k % 6 == 0), f"{c['city']}, {'Karnataka' if 'ang' in c['city'] or 'eng' in c['city'] else 'India'}",
                 "₹15,000 - ₹22,000 a month", "Full-time", "3.9", str(10 + k), f"https://in.indeed.com/viewjob?jk={k:016x}",
                 f"{k:016x}", ["Today", "Just posted", "3 days ago", "30+ days ago", "Posted 7 days ago"][k % 5],
                 f"https://careers.{c['slug']}.example/apply/{k}" if k % 3 == 0 else "", "2026-11-03T05:00:00.000Z"])
write("indeed_apify.csv", ["positionName", "company", "location", "salary", "jobType", "rating", "reviewsCount", "url",
                           "id", "postedAt", "externalApplyLink", "scrapedAt"], rows)

# 4. LinkedIn Jobs scrape: 60 rows
rows = []
for k in range(60):
    c = companies[(k * 11) % 40]
    rows.append([ROLES[(k + 5) % 10], cname(c), f"https://www.linkedin.com/company/{c['slug']}-example",
                 f"{c['city']}, {'Haryana' if 'Gur' in c['city'] else 'India'}", f"{1 + k % 4} weeks ago",
                 f"2026-10-{1 + k % 28:02d}", f"https://www.linkedin.com/jobs/view/{4000000000 + k}",
                 "Over 200 applicants" if k % 2 else "47 applicants", "Full-time", "Entry level", "Staffing and Recruiting"])
write("linkedin_jobs.csv", ["title", "companyName", "companyUrl", "location", "postedTime", "publishedAt", "jobUrl",
                            "applicationsCount", "contractType", "experienceLevel", "sector"], rows)

# 5. Shine / foundit / Apna small exports (Excel-ish)
rows = [[ROLES[k % 10], cname(companies[k]), companies[k]["city"], "0 to 2 Yrs", f"{10 + k}-Oct-26",
         f"https://www.shine.com/jobs/x/{k}"] for k in range(20)]
write("shine.csv", ["Job Title", "Company", "Location", "Experience", "Posted Date", "Job URL"], rows, crlf=True)
rows = [[ROLES[k % 10], cname(companies[k + 10]), f"{companies[k + 10]['city']}, Mumbai", "1-3 Years",
         f"{20 + k % 9}/10/2026", f"https://www.foundit.in/job/x-{k}", str(k % 3 + 1)] for k in range(20)]
write("foundit.csv", ["jobTitle", "companyName", "locations", "experience", "postedDate", "jobUrl", "openings"], rows)
rows = [[ROLES[k % 10], cname(companies[k + 20]), companies[k + 20]["city"], "Sector 18", str(5 + k),
         f"2026-10-{10 + k:02d}", "₹12000 - ₹18000"] for k in range(20)]
write("apna.csv", ["job_title", "company_name", "city", "area", "no_of_openings", "posted_on", "salary"], rows)

# 6. IndiaMART Lead Manager export: 50 buyer enquiries (people at companies)
rows = []
for k in range(50):
    c = companies[k % 40]
    rows.append([f"{2700000000 + k}", "W", f"2026-10-{1 + k % 28:02d} 1{k % 10}:2{k % 6}:00",
                 f"{FIRST[k % 12]} {LAST[k % 6]}" if k % 9 else "Buyer", mobile(k, 3), f"buyer{k}@{c['slug']}.example",
                 "Requirement for AI interview software", cname(c), f"Plot {k}, Industrial Area, {c['city']}",
                 c["city"], "", f"{c['pin']}0{k % 90 + 10:02d}", "IN", mobile(k + 500, 0), landline(c, k),
                 "AI Interview Scheduling Software", "Need for 200 hires/month", "HR Software",
                 "+91-9000099999", "27AAAAA0000A1Z5"])
write("indiamart_leads.csv", ["UNIQUE_QUERY_ID", "QUERY_TYPE", "QUERY_TIME", "SENDER_NAME", "SENDER_MOBILE",
                              "SENDER_EMAIL", "SUBJECT", "SENDER_COMPANY", "SENDER_ADDRESS", "SENDER_CITY", "SENDER_STATE",
                              "SENDER_PINCODE", "SENDER_COUNTRY_ISO", "SENDER_MOBILE_ALT", "SENDER_PHONE",
                              "QUERY_PRODUCT_NAME", "QUERY_MESSAGE", "QUERY_MCAT_NAME", "RECEIVER_MOBILE", "GSTIN"], rows)

# 7. JustDial scrape (Apify style), semicolon + BOM: 60 rows
rows = []
for k in range(60):
    c = companies[(k * 13) % 40]
    phone = landline(c, k) if k % 3 == 0 else (mobile(k + 100, k) if k % 3 == 1 else f"{mobile(k + 100, 0)}, {landline(c, k)}")
    rows.append([cname(c, upper=k % 4 == 0), phone,
                 f"Shop No {k}, {['MG Road', 'Sector 29', 'Andheri East', 'Koramangala 5th Block'][k % 4]}, {c['city']} - {c['pin']}0{k % 90 + 10:02d}",
                 "4.2", f"{k * 3} Ratings", KINDS[c["i"] % 10] + " Services", c["city"], "Verified" if k % 2 else "",
                 f"https://www.justdial.com/{c['city']}/x-{k}"])
write("justdial_apify.csv", ["name", "phone", "address", "rating", "reviews", "category", "city", "verified", "url"],
      rows, delimiter=";", bom=True)

# 8. TradeIndia seller list: 50 rows with GST noise
rows = []
for k in range(50):
    c = companies[(k * 17) % 40]
    rows.append([cname(c, upper=k % 3 == 0), f"Mr. {FIRST[k % 12].upper()} {LAST[k % 6].upper()}", mobile(k + 200, k),
                 landline(c, k + 1), f"{k} Udyog Vihar Phase {1 + k % 5}, {c['city']}, {c['pin']}0{k % 90 + 10:02d}",
                 c["city"], "", f"{c['pin']}0{k % 90 + 10:02d}", f"0{k % 9 + 1}AAACZ{k:04d}Q1Z{k % 9}",
                 "Uniforms, Safety Shoes", str(1990 + k % 30), f"www.{c['slug']}.example" if k % 2 else ""])
write("tradeindia.csv", ["Company Name", "Contact Person", "Mobile", "Phone", "Address", "City", "State", "Pincode",
                         "GST No", "Products", "Year of Establishment", "Website"], rows)

# 9. Outscraper Google Maps: 80 rows
rows = []
for k in range(80):
    c = companies[k % 40]
    phone = [landline(c, k), f"+91 {mobile(k + 300, 0)[:5]} {mobile(k + 300, 0)[5:]}", ""][k % 3]
    rows.append([cname(c), f"https://www.{c['slug']}.example/" if k % 2 else "", "Corporate office, Employment agency",
                 "Corporate office", "Corporate office", phone,
                 f"{k}, {['Outer Ring Rd', 'DLF Cyber City', 'BKC'][k % 3]}, {c['city']}, {'Karnataka' if 'ang' in c['city'] else 'India'} {c['pin']}0{k % 90 + 10:02d}",
                 f"{k}, Main Rd", c["city"], f"{c['pin']}0{k % 90 + 10:02d}", "", "India", "IN", "12.97", "77.59",
                 "4.1", str(k * 2), f"ChIJfake{k:06d}", f"0x0:0x{k:x}", str(1000 + k),
                 f"https://www.google.com/maps/place/?q=place_id:ChIJfake{k:06d}", "OPERATIONAL"])
write("outscraper_gmaps.csv", ["name", "site", "subtypes", "category", "type", "phone", "full_address", "street",
                               "city", "postal_code", "state", "country", "country_code", "latitude", "longitude",
                               "rating", "reviews", "place_id", "google_id", "cid", "location_link", "business_status"],
      rows)

# 10. Apify Google Maps Scraper: 40 rows
rows = []
for k in range(40):
    c = companies[(k * 3 + 1) % 40]
    rows.append([cname(c), "Staffing agency", f"{k} Ring Rd, {c['city']}, {c['pin']}0{k % 90 + 10:02d}", f"{k} Ring Rd",
                 c["city"], f"{c['pin']}0{k % 90 + 10:02d}", "", "IN", f"https://{c['slug']}.example",
                 landline(c, k + 2) if k % 2 else mobile(k + 400, 1), "4.5", str(k), f"ChIJapify{k:05d}",
                 f"https://www.google.com/maps/search/?api=1&query_place_id=ChIJapify{k:05d}"])
write("apify_gmaps.csv", ["title", "categoryName", "address", "street", "city", "postalCode", "state", "countryCode",
                          "website", "phone", "totalScore", "reviewsCount", "placeId", "url"], rows)

# 11. D7 Lead Finder: 30 rows
rows = [[cname(companies[k]), landline(companies[k], k), f"info@{companies[k]['slug']}.example",
         f"{companies[k]['slug']}.example", f"{k} Park St", companies[k]["city"], "", companies[k]["pin"] + "001", "India",
         "Employment agency", "4.0", str(k), f"https://facebook.com/{companies[k]['slug']}", ""] for k in range(30)]
write("d7_leads.csv", ["Name", "Phone", "Email", "Website", "Address", "City", "State", "Zip", "Country", "Category",
                       "Rating", "Reviews", "Facebook", "Instagram"], rows)

# 12. Later contact rows (team sheet) for 10 of the companies seen on job boards
rows = [[cname(companies[k]).upper() if k % 2 else companies[k]["base"], f"{FIRST[k]} {LAST[k % 6]}", "HR Manager",
         f"{FIRST[k].lower()}@{companies[k]['slug']}.example", mobile(k + 900, k),
         "Bengaluru" if "ang" in companies[k]["city"] else companies[k]["city"]] for k in range(10)]
write("contacts_followup.csv", ["Company Name", "Contact Person", "Designation", "Email ID", "Contact Number", "City"],
      rows)
print("ok", len(list(OUT.glob("*.csv"))))
