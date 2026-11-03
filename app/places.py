"""Where and how big: Indian city/state/country clean-up, 'City, State, Country' and postal addresses, company-size
buckets, opening counts and job ages. Pure functions."""
import re
from datetime import date, datetime

from .standardise import clean_text, smart_title

CITY_ALIASES = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "bengalooru": "Bengaluru", "blr": "Bengaluru",
    "bangalore urban": "Bengaluru", "bengaluru urban": "Bengaluru", "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "ggn": "Gurugram", "bombay": "Mumbai", "mumbai": "Mumbai", "greater mumbai": "Mumbai", "mumbai suburban": "Mumbai",
    "navi mumbai": "Navi Mumbai", "new mumbai": "Navi Mumbai", "thane": "Thane", "madras": "Chennai", "chennai": "Chennai",
    "calcutta": "Kolkata", "kolkata": "Kolkata", "poona": "Pune", "pune": "Pune", "baroda": "Vadodara", "vadodara": "Vadodara",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram", "cochin": "Kochi", "kochi": "Kochi",
    "mysore": "Mysuru", "mysuru": "Mysuru", "mangalore": "Mangaluru", "mangaluru": "Mangaluru", "vizag": "Visakhapatnam",
    "visakhapatnam": "Visakhapatnam", "vishakhapatnam": "Visakhapatnam", "pondicherry": "Puducherry",
    "puducherry": "Puducherry", "benares": "Varanasi", "banaras": "Varanasi", "varanasi": "Varanasi",
    "allahabad": "Prayagraj", "prayagraj": "Prayagraj", "calicut": "Kozhikode", "kozhikode": "Kozhikode",
    "trichy": "Tiruchirappalli", "tiruchirappalli": "Tiruchirappalli", "tiruchirapalli": "Tiruchirappalli",
    "belgaum": "Belagavi", "belagavi": "Belagavi", "hubli": "Hubballi", "hubballi": "Hubballi", "noida": "Noida",
    "greater noida": "Greater Noida", "new delhi": "New Delhi", "delhi": "Delhi", "delhi ncr": "Delhi NCR",
    "ncr": "Delhi NCR", "delhi-ncr": "Delhi NCR", "national capital region": "Delhi NCR", "new delhi ncr": "Delhi NCR",
    "hyderabad": "Hyderabad", "secunderabad": "Secunderabad", "ahmedabad": "Ahmedabad", "amdavad": "Ahmedabad",
    "jaipur": "Jaipur", "lucknow": "Lucknow", "chandigarh": "Chandigarh", "indore": "Indore", "bhopal": "Bhopal",
    "nagpur": "Nagpur", "surat": "Surat", "coimbatore": "Coimbatore", "kanpur": "Kanpur", "ludhiana": "Ludhiana",
    "patna": "Patna", "bhubaneswar": "Bhubaneswar", "bhubaneshwar": "Bhubaneswar", "guwahati": "Guwahati",
    "ghaziabad": "Ghaziabad", "faridabad": "Faridabad", "mohali": "Mohali", "goa": "Goa", "panaji": "Panaji",
    "nashik": "Nashik", "nasik": "Nashik", "aurangabad": "Aurangabad", "rajkot": "Rajkot", "dehradun": "Dehradun",
    "ranchi": "Ranchi", "raipur": "Raipur", "vijayawada": "Vijayawada", "madurai": "Madurai", "agra": "Agra",
    "meerut": "Meerut", "amritsar": "Amritsar", "jodhpur": "Jodhpur", "udaipur": "Udaipur", "gandhinagar": "Gandhinagar",
    "manesar": "Manesar", "sonipat": "Sonipat", "zirakpur": "Zirakpur", "panchkula": "Panchkula",
}
CITY_STATE = {
    "Bengaluru": "Karnataka", "Mysuru": "Karnataka", "Mangaluru": "Karnataka", "Belagavi": "Karnataka", "Hubballi": "Karnataka",
    "Gurugram": "Haryana", "Faridabad": "Haryana", "Manesar": "Haryana", "Sonipat": "Haryana", "Panchkula": "Haryana",
    "Mumbai": "Maharashtra", "Navi Mumbai": "Maharashtra", "Thane": "Maharashtra", "Pune": "Maharashtra",
    "Nagpur": "Maharashtra", "Nashik": "Maharashtra", "Aurangabad": "Maharashtra", "Chennai": "Tamil Nadu",
    "Coimbatore": "Tamil Nadu", "Madurai": "Tamil Nadu", "Tiruchirappalli": "Tamil Nadu", "Kolkata": "West Bengal",
    "Hyderabad": "Telangana", "Secunderabad": "Telangana", "Ahmedabad": "Gujarat", "Surat": "Gujarat", "Vadodara": "Gujarat",
    "Rajkot": "Gujarat", "Gandhinagar": "Gujarat", "Noida": "Uttar Pradesh", "Greater Noida": "Uttar Pradesh",
    "Ghaziabad": "Uttar Pradesh", "Lucknow": "Uttar Pradesh", "Kanpur": "Uttar Pradesh", "Varanasi": "Uttar Pradesh",
    "Prayagraj": "Uttar Pradesh", "Agra": "Uttar Pradesh", "Meerut": "Uttar Pradesh", "New Delhi": "Delhi", "Delhi": "Delhi",
    "Jaipur": "Rajasthan", "Jodhpur": "Rajasthan", "Udaipur": "Rajasthan", "Chandigarh": "Chandigarh", "Mohali": "Punjab",
    "Zirakpur": "Punjab", "Ludhiana": "Punjab", "Amritsar": "Punjab", "Indore": "Madhya Pradesh", "Bhopal": "Madhya Pradesh",
    "Kochi": "Kerala", "Thiruvananthapuram": "Kerala", "Kozhikode": "Kerala", "Visakhapatnam": "Andhra Pradesh",
    "Vijayawada": "Andhra Pradesh", "Patna": "Bihar", "Bhubaneswar": "Odisha", "Guwahati": "Assam", "Puducherry": "Puducherry",
    "Goa": "Goa", "Panaji": "Goa", "Dehradun": "Uttarakhand", "Ranchi": "Jharkhand", "Raipur": "Chhattisgarh",
}
STATES = ("Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
          "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
          "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
          "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Chandigarh", "Puducherry", "Jammu and Kashmir", "Ladakh")
STATE_ALIASES = {s.lower(): s for s in STATES}
STATE_ALIASES.update({
    "tamilnadu": "Tamil Nadu", "orissa": "Odisha", "uttaranchal": "Uttarakhand", "nct of delhi": "Delhi",
    "new delhi": "Delhi", "j&k": "Jammu and Kashmir", "jammu & kashmir": "Jammu and Kashmir", "pondicherry": "Puducherry",
    "ka": "Karnataka", "mh": "Maharashtra", "tn": "Tamil Nadu", "dl": "Delhi", "up": "Uttar Pradesh", "hr": "Haryana",
    "ts": "Telangana", "tg": "Telangana", "ap": "Andhra Pradesh", "wb": "West Bengal", "gj": "Gujarat", "rj": "Rajasthan",
    "kl": "Kerala", "mp": "Madhya Pradesh", "pb": "Punjab", "od": "Odisha", "br": "Bihar", "ch": "Chandigarh",
    "py": "Puducherry", "ga": "Goa", "jh": "Jharkhand", "cg": "Chhattisgarh", "uk": "Uttarakhand", "hp": "Himachal Pradesh",
})
COUNTRY_ALIASES = {
    "india": "IN", "in": "IN", "ind": "IN", "bharat": "IN", "republic of india": "IN", "united states": "US",
    "united states of america": "US", "usa": "US", "us": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "gb": "GB", "great britain": "GB", "england": "GB", "britain": "GB",
    "united arab emirates": "AE", "uae": "AE", "ae": "AE", "dubai": "AE", "singapore": "SG", "sg": "SG",
    "australia": "AU", "au": "AU", "canada": "CA", "ca": "CA", "germany": "DE", "de": "DE", "france": "FR",
    "netherlands": "NL", "saudi arabia": "SA", "ksa": "SA", "qatar": "QA", "nepal": "NP", "bangladesh": "BD",
    "sri lanka": "LK", "malaysia": "MY", "philippines": "PH", "indonesia": "ID", "japan": "JP", "south africa": "ZA",
    "kenya": "KE", "nigeria": "NG", "new zealand": "NZ", "ireland": "IE", "oman": "OM", "kuwait": "KW", "bahrain": "BH",
}
_AREA_WORDS = re.compile(r"^greater\s+|\s+(?:area|metropolitan region|metro area|region|district|urban)$", re.IGNORECASE)
PINCODE = re.compile(r"\b([1-9]\d{2})\s?(\d{3})\b")


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z&\- ]+", " ", text.lower())).strip()


def clean_city(raw) -> str:
    """Bangalore -> Bengaluru, Gurgaon -> Gurugram, Bombay -> Mumbai, 'Greater Bengaluru Area' -> Bengaluru."""
    text = clean_text(raw)
    if not text:
        return ""
    key = _key(text)
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    trimmed = _key(_AREA_WORDS.sub("", text))
    head = _key(re.split(r"[(/]", text)[0])            # job boards: 'Bengaluru(Whitefield)', 'Bangalore/Bengaluru'
    return CITY_ALIASES.get(trimmed) or CITY_ALIASES.get(head) or smart_title(text)


def city_key(city) -> str:
    """Delhi and New Delhi compare equal; everything else by its clean name."""
    clean = clean_city(city).lower()
    return "delhi" if clean in ("new delhi", "delhi") else clean


def clean_state(raw, country: str = "") -> str:
    text = clean_text(raw)
    if not text:
        return ""
    key = _key(text)
    if len(key) == 2 and country not in ("", "IN"):
        return text.upper()
    return STATE_ALIASES.get(key) or smart_title(text)


def state_for_city(city: str) -> str:
    return CITY_STATE.get(clean_city(city), "")


def clean_country(raw) -> str:
    """India / IND / Bharat -> IN; common country names -> ISO codes; anything else is kept as written."""
    text = clean_text(raw)
    if not text:
        return ""
    low = text.lower().strip(" .")
    return COUNTRY_ALIASES.get(low) or COUNTRY_ALIASES.get(low.replace(".", "")) or COUNTRY_ALIASES.get(_key(text)) or smart_title(text)


def is_indian_place(city: str = "", state: str = "") -> bool:
    return clean_city(city) in CITY_STATE or clean_state(state) in STATES


def parse_location(raw) -> dict:
    """'Bengaluru, Karnataka, India' / 'Greater Mumbai Area' / 'Pune' -> {city, state, country}."""
    parts = [p.strip() for p in clean_text(raw).split(",") if p.strip()]
    found = {"city": "", "state": "", "country": ""}
    rest = []
    for part in parts:
        bare = PINCODE.sub("", part).strip()
        if not bare:
            continue
        key = _key(bare)
        if not found["country"] and key in COUNTRY_ALIASES and len(key) > 2:
            found["country"] = COUNTRY_ALIASES[key]
        elif not found["state"] and key in STATE_ALIASES and len(key) > 2 and (rest or key not in CITY_ALIASES):
            found["state"] = STATE_ALIASES[key]
        elif not found["city"] and (key in CITY_ALIASES or _key(_AREA_WORDS.sub("", bare)) in CITY_ALIASES):
            found["city"] = clean_city(bare)
        else:
            rest.append(bare)
    if not found["city"] and rest and len(parts) <= 3:
        found["city"] = clean_city(rest[0])
    return found


def parse_address(raw) -> dict:
    """Postal address -> {city, state, country, pincode}; only parts we can recognise are returned."""
    text = clean_text(raw)
    pin = PINCODE.search(text)
    found = {"city": "", "state": "", "country": "", "pincode": (pin.group(1) + pin.group(2)) if pin else ""}
    for part in reversed([p.strip() for p in re.split(r"[,\n]", text) if p.strip()]):
        bare = _key(PINCODE.sub("", part))
        words = bare.replace("-", " ").split()
        candidates = [bare] + [" ".join(words[i:i + n]) for n in (2, 1) for i in range(len(words) - n + 1)]
        for cand in candidates:
            if not found["country"] and cand in COUNTRY_ALIASES and len(cand) > 2:
                found["country"] = COUNTRY_ALIASES[cand]
            elif not found["state"] and cand in STATE_ALIASES and len(cand) > 2 and cand not in CITY_ALIASES:
                found["state"] = STATE_ALIASES[cand]
            elif not found["city"] and cand in CITY_ALIASES:
                found["city"] = CITY_ALIASES[cand]
    if found["pincode"] and not found["country"]:
        found["country"] = "IN"
    return found


# ---------------------------------------------------------------- sizes, openings, job age
SIZE_BUCKETS = ((10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (1000, "501-1000"))
_WORD_SIZES = {"self-employed": 1, "self employed": 1, "myself only": 1, "freelance": 1}


def size_bucket(count: int) -> str:
    if not count or count <= 0:
        return ""
    return next((label for top, label in SIZE_BUCKETS if count <= top), "1000+")


def parse_company_size(raw) -> tuple:
    """'51-200' -> (125, '51-200'); '1,000+' -> (1000, '1000+'); '# Employees: 35' -> (35, '11-50'); junk -> (0, '')."""
    text = clean_text(raw).lower().replace("–", "-").replace("—", "-")
    if text in _WORD_SIZES:
        return 1, "1-10"
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)[:2]]
    if not numbers or not re.match(r"^[\s~<>a-z:]*\d", text):
        return 0, ""
    if len(numbers) == 2 and re.search(r"\d\s*(?:-|to)\s*\d", text):
        low, high = sorted(numbers)
        estimate = (low + high) // 2
        return estimate, size_bucket(estimate)
    count = numbers[0]
    if "+" in text or "more than" in text or ">" in text:
        return count, size_bucket(count + 1)
    return count, size_bucket(count)


def parse_count(raw) -> int:
    """'5', '10+ openings', '2 positions' -> the first whole number; anything else 0."""
    match = re.search(r"\d[\d,]*", clean_text(raw))
    return int(match.group(0).replace(",", "")) if match else 0


_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y",
                 "%b %d, %Y", "%B %d, %Y", "%d/%m/%y", "%Y/%m/%d")


def job_age_days(raw, today: date) -> int:
    """'3 days ago', '30+ days ago', 'Just now', '2 weeks ago', '13-Oct-2026', '2026-10-13' -> age in days (0 if unknown)."""
    text = clean_text(raw).lower()
    if not text:
        return 0
    if text in ("today", "just now", "just posted", "few hours ago") or "hour" in text or "minute" in text:
        return 0
    if text == "yesterday":
        return 1
    text = re.sub(r"^(?:posted|active|updated)\s+", "", text)
    rel = re.match(r"^(\d+)\+?\s*(day|week|month)s?\s*(?:ago)?", text)
    if rel:
        return int(rel.group(1)) * {"day": 1, "week": 7, "month": 30}[rel.group(2)]
    if re.fullmatch(r"[2-6]\d{4}(?:\.0+)?", text):            # Excel serial date, e.g. 46316
        return max(0, (today - date(1899, 12, 30)).days - int(float(text)))
    day = re.split(r"[T ]\d{1,2}:\d{2}", clean_text(raw)[:30])[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return max(0, (today - datetime.strptime(day, fmt).date()).days)
        except ValueError:
            continue
    return 0
