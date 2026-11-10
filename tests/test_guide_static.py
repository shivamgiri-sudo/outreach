"""Static checks for the glossary, the <Abbr> tooltips and the user guide (web/src/glossary*.js, web/src/guide/, Help page).

The guide chapters and the glossary are plain data, so Node loads the real files (tests/guide_dump.mjs) and these tests
read the JSON. Menu labels come from web/src/App.jsx and web/src/pages/Setup.jsx, so a new page or Setup section without
a chapter fails here. Add a chapter by adding one file in web/src/guide/chapters/.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
PUBLIC = ROOT / "web" / "public" / "guide"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# Every abbreviation and plain-English term the portal shows that must be explained.
ABBREVIATIONS = ["MQL", "SAL", "SQL", "BANT", "ICP", "SLA", "KPI", "CTA", "CSV", "IST", "TCV", "CXO", "DND", "DPDP", "2FA", "TOTP", "OIDC", "SSO",
                 "API", "MCP", "QR", "DLT", "SPF", "DKIM", "DMARC", "IMAP", "SMS", "URL", "PDF", "AI", "OTP", "ID", "PII"]
TERMS = ["A/B test", "consent", "unsubscribe", "bounce", "hard bounce", "soft bounce", "spam complaint", "open rate", "click tracking", "cold email",
         "sequence", "snippet", "template", "flow", "workspace", "account", "owner", "admin", "caller", "viewer", "super admin", "two-step login",
         "recovery code", "webhook", "connector", "lead source", "Meta Lead Ads", "Apollo", "Hunter", "Firecrawl", "enrichment", "warm-up",
         "practice mode", "send for real", "stalled lead", "speed to lead", "pipeline coverage", "sales velocity", "win rate", "weighted pipeline",
         "cohort", "recycled", "nurture", "back-filled stage date", "change log", "hash chain", "audit pack", "pairing code", "office computer",
         "LinkedIn agent", "connection request", "Open Profile"]
KINDS = {"h", "p", "steps", "ul", "tip", "warn", "table", "img"}
BANNED = re.compile(r"\b(UTC|GMT|undefined|NaN|null|Invalid Date)\b")


@pytest.fixture(scope="module")
def dump():
    out = subprocess.run(["node", str(ROOT / "tests" / "guide_dump.mjs"), str(SRC)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def menu_labels():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    pages = re.findall(r'^\s*\["(\w+)", "([^"]+)", \w+, "\w+"\],', app, re.M)
    setup = (SRC / "pages" / "Setup.jsx").read_text(encoding="utf-8")
    sections = re.findall(r'^\s*\["(\w+)", "([^"]+)", (?:OWNER|ADMINS|EVERYONE|\[[^\]]*\]),', setup, re.M)
    return pages, sections


def text_of(chapter):
    out = [chapter["title"], chapter.get("summary", "")]
    for kind, a, *rest in chapter["blocks"]:
        if kind in ("steps", "ul"):
            out += a
        elif kind == "table":
            out += a["head"] + [c for r in a["rows"] for c in r]
        else:
            out += [a] + [x for x in rest if isinstance(x, str)]
    return out


# ---- glossary
def test_every_listed_abbreviation_is_explained(dump):
    by = {g["term"].lower(): g for g in dump["glossary"]}
    for term in ABBREVIATIONS:
        g = by.get(term.lower())
        assert g, f"{term} is missing from the glossary"
        assert g["full"].strip(), f"{term} has no full name"
        assert g["short"].strip() and g["long"].strip() and g["where"].strip(), f"{term} needs a short meaning, a longer explanation and where it is used"


def test_every_listed_term_has_an_entry(dump):
    have = {norm(g["term"]) for g in dump["glossary"]}
    # "recycled" and "nurture" are covered by Recycled / Nurture; "super admin" etc. by their own entries
    aliases = {"nurture": "recycled nurture", "send for real": "send for real", "recycled": "recycled"}
    missing = [t for t in TERMS if norm(aliases.get(t, t)) not in have]
    assert not missing, f"glossary has no entry for: {missing}"


def test_glossary_has_no_duplicate_terms(dump):
    terms = [norm(g["term"]) for g in dump["glossary"]]
    ids = [g["id"].lower() for g in dump["glossary"]]
    assert len(terms) == len(set(terms)), sorted({t for t in terms if terms.count(t) > 1})
    assert len(ids) == len(set(ids))


def test_glossary_entries_are_complete_and_plain(dump):
    for g in dump["glossary"]:
        for key in ("term", "short", "long", "where"):
            assert g[key].strip(), f"{g['term']}: {key} is empty"
        assert not BANNED.search(g["short"] + g["long"]), g["term"]


def test_abbr_usages_exist_in_the_glossary(dump):
    have = {re.sub(r"[^A-Za-z0-9]+", "-", g["term"]).strip("-").lower() for g in dump["glossary"]}
    seen = set()
    for f in list(SRC.rglob("*.jsx")):
        for term in re.findall(r'<Abbr term="([^"]+)"', f.read_text(encoding="utf-8")):
            seen.add(term)
            assert re.sub(r"[^A-Za-z0-9]+", "-", term).strip("-").lower() in have, f"{f.name}: <Abbr term=\"{term}\"> is not in the glossary"
    assert len(seen) >= 12, "the UI should use <Abbr> in many places"


# ---- guide
def test_every_menu_page_and_setup_section_has_a_chapter(dump):
    pages, sections = menu_labels()
    assert len(pages) >= 25 and len(sections) >= 12, "the menu regex found too little; update the test with the code"
    chapters = {c["id"]: c for c in dump["chapters"]}
    by_menu = {(c["kind"], c.get("menu")) for c in dump["chapters"]}
    for pid, label in pages:
        assert ("page", label) in by_menu, f"menu page '{label}' ({pid}) has no chapter (kind page, menu '{label}')"
        assert pid in chapters and chapters[pid]["menu"] == label, f"the chapter id for '{label}' should be '{pid}'"
    for sid, label in sections:
        assert ("setup", label) in by_menu, f"Setup section '{label}' has no chapter"
        assert f"setup-{sid}" in chapters, f"the chapter id for Setup '{label}' should be 'setup-{sid}'"


def test_chapters_are_well_formed(dump):
    ids = [c["id"] for c in dump["chapters"]]
    assert len(ids) == len(set(ids)), "two chapters share an id"
    for c in dump["chapters"]:
        assert re.fullmatch(r"[a-z0-9-]+", c["id"]), c["id"]
        assert c["group"] in dump["groups"], f"{c['id']}: unknown group"
        assert c.get("summary", "").strip() and c["blocks"], c["id"]
        assert c["kind"] in ("start", "page", "setup", "recipe", "faq", "topic"), c["id"]
        assert set(c.get("roles", ["owner", "admin", "caller", "viewer"])) <= {"owner", "admin", "caller", "viewer"}, c["id"]
        assert c["lines"] <= 500, f"{c['file']} is over 500 lines; split it"
        for b in c["blocks"]:
            assert b[0] in KINDS, f"{c['id']}: unknown block {b[0]}"
        assert not BANNED.search(" ".join(text_of(c))), f"{c['id']}: contains a word the screen must never show ({BANNED.search(' '.join(text_of(c))).group(0)})"


def test_chapters_only_name_real_menu_labels(dump):
    pages, sections = menu_labels()
    real = {l for _, l in pages} | {l for _, l in sections}
    for c in dump["chapters"]:
        for t in text_of(c):
            for label in re.findall(r"\{\{menu:([^}]+)\}\}", t):
                assert label in real, f"{c['id']}: '{label}' is not a menu page or Setup section"


def test_chapter_links_abbr_and_images_resolve(dump):
    ids = {c["id"] for c in dump["chapters"]}
    terms = {re.sub(r"[^A-Za-z0-9]+", "-", g["term"]).strip("-").lower() for g in dump["glossary"]}
    for c in dump["chapters"]:
        for t in text_of(c):
            for target in re.findall(r"\]\(#help/([^)]*)\)", t):
                head, _, rest = target.partition("/")
                if head == "glossary":
                    assert not rest or rest.lower() in terms, f"{c['id']}: no glossary term '{rest}'"
                else:
                    assert head in ids, f"{c['id']}: link to missing chapter '{head}'"
            for term in re.findall(r"\[\[([^\]|]+)", t):
                assert re.sub(r"[^A-Za-z0-9]+", "-", term).strip("-").lower() in terms, f"{c['id']}: [[{term}]] is not in the glossary"
        for b in c["blocks"]:
            if b[0] == "img":
                f = PUBLIC / f"{b[1]}.webp"
                assert f.exists(), f"{c['id']}: screenshot {b[1]}.webp is missing"
                assert len(b[2].strip()) > 10, f"{c['id']}: screenshot {b[1]} needs alt text"


def test_screenshots_are_small():
    if PUBLIC.exists():
        for f in PUBLIC.glob("*"):
            assert f.stat().st_size <= 150 * 1024, f"{f.name} is {f.stat().st_size // 1024} KB (limit 150 KB)"


def test_help_links_point_at_real_chapters(dump):
    ids = {c["id"] for c in dump["chapters"]}
    used = set()
    for f in SRC.rglob("*.jsx"):
        for chapter in re.findall(r'<HelpLink chapter="([^"]+)"', f.read_text(encoding="utf-8")):
            used.add(chapter)
            assert chapter in ids, f"{f.name}: <HelpLink chapter=\"{chapter}\"> has no chapter"
    assert {"funnel", "leads", "linkedin", "outbox", "reports"} <= used, "contextual ? links are missing on key pages"
    setup_link = any("chapter={`setup-" in f.read_text(encoding="utf-8") for f in SRC.rglob("*.jsx"))
    assert setup_link, "Setup sections need a ? link"


def test_guide_roles_cover_sensitive_chapters(dump):
    by = {c["id"]: c for c in dump["chapters"]}
    assert by["setup-accounts"].get("masOnly") and by["setup-accounts"]["roles"] == ["owner"]
    assert set(by["setup-connections"]["roles"]) <= {"owner", "admin"}
    assert set(by["leads"].get("roles", ["owner", "admin", "caller", "viewer"])) == {"owner", "admin", "caller", "viewer"}
