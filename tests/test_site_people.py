"""Wider own-site read and name + email pairing (app/enrich.py, app/site_people.py). Fake pages only; names are made up."""
import json

from app.enrich import MAX_PAGES_PER_DOMAIN, find_public_contacts, linked_pages
from app.site_people import looks_like_name, pairs

CARDS = ('<div class="team"><div class="member-card"><h4>Asha Rao</h4><span>CEO</span>'
         '<a href="mailto:asha@acme.in">mail</a></div>'
         '<div class="member-card"><h4>Rohit Menon</h4><a href="mailto:press@acme.in">press</a></div></div>')


def test_mailto_on_the_same_card_pairs_name_and_email():
    assert pairs(CARDS, "https://acme.in/team") == [{"name": "Asha Rao", "email": "asha@acme.in", "title": "", "source": "https://acme.in/team"}]


def test_without_a_card_edge_the_name_must_be_in_the_address():
    loose = "<h2>Asha Rao</h2><p>Some text</p><a href='mailto:sales.team@acme.in'>x</a><a href='mailto:asha.rao@acme.in'>y</a>"
    assert [p["email"] for p in pairs(loose, "u")] == ["asha.rao@acme.in"]


def test_schema_org_person_is_paired_and_privacy_addresses_never_are():
    data = [{"@type": "Person", "name": "Vikram Shah", "jobTitle": "Head of HR", "email": "vikram@acme.in"},
            {"@type": "Person", "name": "Data Officer Desk", "email": "dpo@acme.in"}]
    html = f'<script type="application/ld+json">{json.dumps(data)}</script><li><b>Neha Iyer</b><a href="mailto:privacy@acme.in">x</a></li>'
    assert pairs(html, "u") == [{"name": "Vikram Shah", "email": "vikram@acme.in", "title": "Head of HR", "source": "u"}]


def test_looks_like_name():
    assert looks_like_name("Asha Rao") and looks_like_name("Maria de la Cruz")
    assert not looks_like_name("Contact us") and not looks_like_name("Asha") and not looks_like_name("Call 98111 22233")


def test_wide_read_follows_home_links_skips_not_for_sales_and_caps_pages():
    home = ('<a href="/company/leadership">Leaders</a><a href="https://other.in/team">x</a>'
            ' privacy@acme.in abuse@acme.in noreply@acme.in postmaster@acme.in hello@acme.in')
    pages = {"https://acme.in/": home, "https://acme.in/company/leadership": CARDS}
    asked = []

    def fetch(url):
        asked.append(url)
        if url in pages:
            return pages[url]
        raise PermissionError("robots.txt does not allow fetching this page")
    found = find_public_contacts("acme.in", fetch, wide=True)
    assert "https://acme.in/company/leadership" in asked and len(asked) == MAX_PAGES_PER_DOMAIN
    assert not any("other.in" in u for u in asked)
    assert set(found["emails"]) == {"hello@acme.in", "asha@acme.in", "press@acme.in"}
    assert found["skipped"] == ["abuse@acme.in", "noreply@acme.in", "postmaster@acme.in", "privacy@acme.in"]
    assert found["people"][0]["name"] == "Asha Rao" and found["pages_checked"] == 2


def test_narrow_read_is_the_usual_five_pages():
    asked = []

    def fetch(url):
        asked.append(url)
        raise OSError("down")
    find_public_contacts("acme.in", fetch)
    assert asked == ["https://acme.in/", "https://acme.in/contact", "https://acme.in/contact-us", "https://acme.in/about", "https://acme.in/careers"]


def test_linked_pages_keeps_same_site_team_and_press_links():
    html = '<a href="/our-team/">t</a><a href="https://www.acme.in/press">p</a><a href="/pricing">x</a><a href="mailto:a@acme.in">m</a>'
    assert linked_pages(html, "https://acme.in/", "acme.in") == ["https://acme.in/our-team", "https://acme.in/press"]
