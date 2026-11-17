"""Lead drawer research helpers (app/lead_research.py): search links URL-encoded, "Ask who handles hiring" offered only for
generic / unnamed contacts, seeded once into the snippet library, never sends by itself, respects suppression."""
from urllib.parse import parse_qs, urlparse

from app import lead_research as LR
from tests.flow_helpers import lead, make_client, patch_lead, store_of


def q_of(url: str) -> str:
    parsed = urlparse(url)
    return parse_qs(parsed.query).get("q", parse_qs(parsed.query).get("keywords", [""]))[0]


def test_search_links_are_encoded():
    links = {l["id"]: l["url"] for l in LR.search_links({"email": "a.b+x@acme.in", "contact_name": 'Rāj "RJ" & Sons',
                                                           "company": "Tata & Sons Pvt. Ltd.", "domain": "acme.in"})}
    assert set(links) == {"email", "site", "linkedin", "careers"}
    assert q_of(links["email"]) == '"a.b+x@acme.in"' and "%22a.b%2Bx%40acme.in%22" in links["email"]
    assert q_of(links["site"]) == 'site:acme.in Rāj "RJ" & Sons contact OR email'
    assert q_of(links["linkedin"]) == 'Rāj "RJ" & Sons Tata & Sons Pvt. Ltd.' and links["linkedin"].startswith("https://www.linkedin.com/search/results/people/")
    assert q_of(links["careers"]) == "Tata & Sons Pvt. Ltd. careers"
    for url in links.values():
        assert " " not in url and '"' not in url and "&S" not in url and "ā" not in url       # nothing raw in the address


def test_search_links_only_when_the_input_exists():
    assert LR.search_links({}) == []
    ids = [l["id"] for l in LR.search_links({"company": "Acme"})]
    assert ids == ["linkedin", "careers"]
    assert LR.search_links({"email": "not-an-email", "company": ""}) == []


def test_generic_only_rule():
    assert LR.generic_only({"email": "info@acme.in", "contact_name": "Asha Rao"})
    assert LR.generic_only({"email": "asha@acme.in", "contact_name": ""})
    assert not LR.generic_only({"email": "asha@acme.in", "contact_name": "Asha Rao"})
    assert not LR.generic_only({"contact_name": ""})


def setup(tmp_path, **fields):
    c = make_client(tmp_path)
    lid = lead(c, "Acme")["id"]
    patch_lead(c, lid, **{"email": "hr@acme.example", "contact_name": "", **fields})
    return c, lid


def test_ask_draft_offered_and_never_sends(tmp_path):
    c, lid = setup(tmp_path)
    before = len(store_of(c).read("outbox"))
    view = c.get(f"/api/leads/{lid}/research").json()
    ask = view["ask"]
    assert ask["offer"] and ask["to"] == "hr@acme.example" and "{{company}}" in ask["subject"] and "hiring or HR" in ask["body"]
    assert "{{sign_off}}" in ask["body"]
    assert len(store_of(c).read("outbox")) == before                   # drafting sends nothing
    assert not any("One-off" in t.get("text", "") for t in store_of(c).read("leads")[0]["timeline"])


def test_template_seeded_once_and_idempotent(tmp_path):
    c, lid = setup(tmp_path)
    for _ in range(3):
        c.get(f"/api/leads/{lid}/research")
    rows = [s for s in c.get("/api/snippets").json() if s.get("builtin") == LR.SNIPPET_KEY]
    assert len(rows) == 1 and rows[0]["name"] == "Ask who handles hiring" and rows[0]["channel"] == "email"
    c.put(f"/api/snippets/{rows[0]['id']}", json={"name": "Ask who handles hiring", "channel": "email", "subject": "Quick question",
                                                  "body": "Hello, who handles hiring at {{company}}?"})
    assert c.get(f"/api/leads/{lid}/research").json()["ask"]["subject"] == "Quick question"   # the owner's wording is used
    c.delete(f"/api/snippets/{rows[0]['id']}")
    ask = c.get(f"/api/leads/{lid}/research").json()["ask"]
    assert ask["offer"] and ask["subject"] == LR.ASK_SNIPPET["subject"]                     # built-in text, not re-seeded
    assert not [s for s in c.get("/api/snippets").json() if s.get("builtin")]


def test_preset_apply_not_blocked_by_the_builtin_snippet(tmp_path):
    c, lid = setup(tmp_path)
    c.get(f"/api/leads/{lid}/research")
    presets = c.get("/api/presets").json()
    pid = (presets[0] if isinstance(presets, list) else presets["presets"][0])["id"]
    out = c.post(f"/api/presets/{pid}/apply", json={}).json()
    assert "snippets" in out.get("changed", []), out


def test_ask_respects_suppression_and_stage(tmp_path):
    c, lid = setup(tmp_path)
    store_of(c).update("suppressed", lambda rows: [*rows, "hr@acme.example"])
    ask = c.get(f"/api/leads/{lid}/research").json()["ask"]
    assert not ask["offer"] and "may not be emailed" in ask["reason"] and ask["body"] == ""
    assert not [s for s in c.get("/api/snippets").json() if s.get("builtin")]               # nothing seeded for a refused lead
    sent = c.post(f"/api/leads/{lid}/email-compose", json={"subject": "x", "body": "{{greeting}} hi"})
    assert sent.status_code == 400                                                           # the send path refuses too


def test_ask_not_offered_for_named_person_or_invalid_or_unsubscribed(tmp_path):
    c, lid = setup(tmp_path, email="asha.rao@acme.example", contact_name="Asha Rao")
    assert not c.get(f"/api/leads/{lid}/research").json()["ask"]["offer"]
    patch_lead(c, lid, email="info@acme.example", email_check={"status": "invalid", "email": "info@acme.example"})
    assert "does not exist" in c.get(f"/api/leads/{lid}/research").json()["ask"]["reason"]
    patch_lead(c, lid, email_check={}, stage="unsubscribed")
    assert not c.get(f"/api/leads/{lid}/research").json()["ask"]["offer"]
    patch_lead(c, lid, stage="new", email="")
    assert c.get(f"/api/leads/{lid}/research").json()["ask"]["reason"] == "This lead has no email address."


def test_gate_note_when_the_mailbox_rule_is_on(tmp_path):
    c, lid = setup(tmp_path)
    store_of(c).update("settings", lambda s: {**s, "email_gate": {"enabled": True, "approve_risky": False}})
    ask = c.get(f"/api/leads/{lid}/research").json()["ask"]
    assert ask["offer"] and ask["gate_note"].startswith("Check before sending")
    patch_lead(c, lid, email_check={"status": "valid", "email": "hr@acme.example"})
    assert c.get(f"/api/leads/{lid}/research").json()["ask"]["gate_note"] == ""


def test_research_is_404_for_unknown_lead(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/leads/nope/research").status_code == 404
