"""Private demo page: a sample screening kit drafted from the lead's job title (not from their candidates)."""
import re
from html import escape
from urllib.parse import quote

GENERIC_ROLE = "this role"
ROLE_PHRASE = "the {name} role"
WEIGHTS = {"Voice answer": 25, "Situational MCQ": 25, "Personality fit": 20, "Résumé match": 30}

# Checked in this order, so "customer support engineer" counts as support and "sales operations" as sales.
FAMILY_KEYWORDS = (
    ("support", r"support|customer service|customer care|customer success|helpdesk|help desk|service desk|"
                r"voice process|chat process|call cent(er|re)|csr|cse"),
    ("sales", r"sales|telecaller|tele caller|telesales|business development|bde|bdm|inside sales|account executive|"
              r"relationship manager|pre-sales|presales|counsell?or|collections?"),
    ("tech", r"developer|engineer|software|programmer|devops|qa|tester|testing|full stack|front ?end|back ?end|"
             r"python|java|data analyst|data scientist|it"),
    ("operations", r"operations|ops|back office|data entry|mis|process associate|coordinator|admin|logistics|"
                   r"warehouse|supply chain|dispatch"),
)
FAMILY_QUESTIONS = {
    "sales": (
        "A prospect says 'send me details on WhatsApp' and then goes quiet. What do you do next? (voice answer)",
        "You are behind target with a week left in the month. What do you do first? (multiple choice)",
        "A customer asks for a discount you cannot approve. How do you respond? (multiple choice)",
    ),
    "support": (
        "How would you calm a customer whose issue is still not fixed after two calls? (voice answer)",
        "A customer is upset and you do not know the answer. What do you do first? (multiple choice)",
        "Your queue is full and one call is running long. How do you handle it? (multiple choice)",
    ),
    "operations": (
        "How do you check your own work for errors before you submit it? (voice answer)",
        "Two urgent tasks arrive at once from different managers. What do you do first? (multiple choice)",
        "You spot a mistake in yesterday's report made by someone else. What do you do? (multiple choice)",
    ),
    "tech": (
        "Describe a problem you fixed recently and how you found the cause. (voice answer)",
        "A release is due today and you find a serious bug. What do you do first? (multiple choice)",
        "A teammate's code works but is hard to read. How do you raise it? (multiple choice)",
    ),
    "other": (
        "Describe a time you handled a difficult situation at work. What did you do? (voice answer)",
        "A task is due today and you are stuck. What do you do first? (multiple choice)",
        "A teammate asks you to cover their shift at short notice. How do you respond? (multiple choice)",
    ),
}


def role_family(role: str) -> str:
    """sales, support, operations, tech or other, from keywords in the job title."""
    text = (role or "").lower()
    return next((family for family, words in FAMILY_KEYWORDS if re.search(rf"\b({words})\b", text)), "other")


def draft_kit(role: str) -> dict:
    name = (role or "").strip() or GENERIC_ROLE
    phrase = GENERIC_ROLE if name == GENERIC_ROLE else ROLE_PHRASE.format(name=name)
    family = role_family(role)
    return {
        "role": name,
        "family": family,
        "questions": [
            f"In 60 seconds, tell us why you are interested in {phrase} (voice answer).",
            *FAMILY_QUESTIONS[family],
            "What notice period and monthly pay do you expect?",
        ],
        "weights": dict(WEIGHTS),
    }


def _stop_link(unsub_path: str) -> str:
    """The lead's signed unsubscribe link (app/unsubscribe.py builds it); nothing when there is none."""
    if not unsub_path:
        return ""
    href = escape(quote(unsub_path, safe="/."), quote=True)
    return f'<p><small><a href="{href}">Not for me — stop emails</a></small></p>'


def _overview_html(lead: dict, offer: dict, claim_url: str, unsub_path: str, brand: dict) -> str:
    """A short product page for any business (offer "demo_kind": "overview"): what it is, who it's for, the three
    value points and the call to action, in the brand's colours and logo."""
    profile = offer.get("offer") or {}
    company = escape(lead.get("company") or "your team")
    product = escape(brand.get("product_name") or "")
    colour = escape(brand.get("primary_color") or "#3b5bdb", quote=True)
    logo = (f'<img src="{escape(brand["logo_url"], quote=True)}" alt="{product}" style="max-height:48px;max-width:200px">'
            if brand.get("logo_url") else f"<b style='font-size:20px'>{product}</b>")
    values = "".join(f"<li>{escape(profile[k])}</li>" for k in ("value_1", "value_2", "value_3") if str(profile.get(k) or "").strip())
    what = escape(str(profile.get("what") or "").strip())
    who = escape(str(profile.get("who") or "").strip())
    cta = escape(str(profile.get("cta") or "").strip() or "Book a short call")
    link = str(profile.get("booking_link") or brand.get("website") or "").strip()
    button = f'<p><a class="btn" href="{escape(link, quote=True)}">Book a time</a></p>' if link.startswith(("https://", "http://")) else ""
    tagline = escape(str(brand.get("tagline") or "").strip())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{product} for {company}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#1c2330}}
header{{border-bottom:3px solid {colour};padding-bottom:12px}}.card{{border:1px solid #e4e8ee;border-radius:10px;padding:16px;margin:16px 0}}
.btn{{display:inline-block;background:{colour};color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none}}small{{color:#6b7686}}</style></head><body>
<header>{logo}{f"<p><small>{tagline}</small></p>" if tagline else ""}</header>
<h1>{product} for {company}</h1>
{f"<p>{what}</p>" if what else ""}{f"<p><small>Made for {who}.</small></p>" if who else ""}
{f'<div class="card"><h3>What you get</h3><ul>{values}</ul></div>' if values else ""}
<div class="card"><h3>Next step</h3><p>{cta}</p>{button}</div>
{_stop_link(unsub_path)}
</body></html>"""


def render_demo_html(lead: dict, kit: dict, offer: dict, claim_url: str, unsub_path: str = "", brand: dict = None) -> str:
    """The private demo page. With the offer's demo_kind "overview" it is a short product page; otherwise the sample
    screening kit. `brand` (app/brand.get_brand) sets the product name, colour and logo; the default looks as before."""
    from .brand import DEFAULT_BRAND
    brand = brand or DEFAULT_BRAND
    if (offer.get("offer") or {}).get("demo_kind") == "overview":
        return _overview_html(lead, offer, claim_url, unsub_path, brand)
    short = "BookMyInterview" if brand.get("product_name") == DEFAULT_BRAND["product_name"] else escape(brand.get("product_name") or "")
    colour = "#3b5bdb" if brand.get("primary_color") == DEFAULT_BRAND["primary_color"] else escape(brand.get("primary_color") or "#3b5bdb", quote=True)
    logo = (f'<p><img src="{escape(brand["logo_url"], quote=True)}" alt="{short}" style="max-height:48px;max-width:200px"></p>'
            if brand.get("logo_url") else "")
    company, role = escape(lead.get("company") or "your team"), escape(kit["role"])
    heading = f"A sample screen for {company}'s {role} opening" if kit["role"] != GENERIC_ROLE else f"A sample screen for {company}"
    questions = "".join(f"<li>{escape(q)}</li>" for q in kit["questions"])
    weights = "".join(f"<li>{escape(k)} — {v}%</li>" for k, v in kit["weights"].items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Sample screen for {role}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#1c2330}}
.card{{border:1px solid #e4e8ee;border-radius:10px;padding:16px;margin:16px 0}}.btn{{display:inline-block;background:{colour};color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none}}
small{{color:#6b7686}}</style></head><body>
{logo}<h1>{heading}</h1>
<p><small>Sample drafted from your job title, not based on your candidates. {short} is in beta, and everything here is editable before you publish.</small></p>
<div class="card"><h3>Questions candidates would answer</h3><ol>{questions}</ol></div>
<div class="card"><h3>How candidates are ranked</h3><p><small>These are default weights, you can change them.</small></p><ul>{weights}</ul>
<p><small>Each score comes with a reason, so you know why someone ranks where they do. The best candidates then book their own interview slot.</small></p></div>
<div class="card"><h3>Try it free</h3>
<p>Your first role is free for {offer['trial_days']} days, up to {offer['candidate_cap']} candidates screened and {offer['booking_cap']} interviews booked. You decide afterwards whether to continue.</p>
<p><a class="btn" href="{escape(claim_url, quote=True)}">Use this draft for my role</a></p></div>
{_stop_link(unsub_path)}
</body></html>"""
