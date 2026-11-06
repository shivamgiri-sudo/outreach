"""Chrome assistant "Save to portal": one profile per click, validated, human-paced, never overwriting, scoped, audited."""
import pytest

from app import extension_save, privacy
from app.lead_model import linkedin_profile_url

from .test_extension_api import ADMIN, URL, bearer, make, match, pair

PATH = "/api/extension/save-profile"
NEW = "https://www.linkedin.com/in/test-person"


def save(c, token, **body):
    body.setdefault("profile_url", NEW)
    body.setdefault("name", "Test Person")
    return c.post(PATH, json=body, headers=bearer(token))


def lead_by_id(c, lid):
    return next(l for l in c.app.state.store.read("leads") if l["id"] == lid)


def audit_rows(c):
    return [r for r in c.app.state.store.read("audit") if r.get("action") == "extension.save_profile"]


def caller(c, owner, email="rep@x.com", name="Rita Rep", workspaces=None):
    c.post("/api/users", json={"email": email, "name": name, "role": "caller", "password": "long-password-1"}, headers=bearer(owner))
    if workspaces is not None:
        c.put(f"/api/users/{email}", json={"workspaces": workspaces}, headers=bearer(owner))
    tok = c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"]
    return pair(c, tok)["token"]


# ---------- URL normalisation ----------
@pytest.mark.parametrize("raw,expected", [
    ("https://www.linkedin.com/in/test-person", NEW),
    ("https://www.linkedin.com/in/test-person/", NEW),
    ("https://in.linkedin.com/in/test-person/?trk=x&utm_source=share", NEW),
    ("linkedin.com/in/test-person", NEW),
    ("http://linkedin.com/in/test-person#about", NEW),
    ("https://www.linkedin.com/in/test-person/details/experience/", NEW),
    ("https://www.linkedin.com/company/acme", ""),
    ("https://www.linkedin.com/pub/test-person/1/2/3", ""),
    ("https://www.linkedin.com/search/results/people/?keywords=hr", ""),
    ("https://www.linkedin.com/feed/", ""),
    ("https://evil.example/in/test-person", ""),
    ("https://linkedin.com.evil.example/in/test-person", ""),
    ("javascript:alert(1)", ""),
    ("", ""),
])
def test_profile_url_normaliser(raw, expected):
    assert linkedin_profile_url(raw) == expected


# ---------- validation ----------
@pytest.mark.parametrize("body,code", [
    ({"profile_url": "https://www.linkedin.com/search/results/people/?keywords=hr"}, 400),
    ({"profile_url": "https://www.linkedin.com/company/acme"}, 400),
    ({"profile_url": "https://evil.example/in/test-person"}, 400),
    ({"name": "   "}, 400),
    ({"name": "​​"}, 400),
    ({"name": "x" * 121}, 422),
    ({"title": "x" * 201}, 422),
    ({"company": "x" * 201}, 422),
    ({"location": "x" * 201}, 422),
    ({"profile_url": "https://www.linkedin.com/in/" + "a" * 2000}, 422),
])
def test_validation(tmp_path, body, code):
    c, owner, _, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    before = len(c.app.state.store.read("leads"))
    r = save(c, t, **body)
    assert r.status_code == code, r.text
    assert len(c.app.state.store.read("leads")) == before


def test_missing_name_is_rejected(tmp_path):
    c, owner, _, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    assert c.post(PATH, json={"profile_url": NEW}, headers=bearer(t)).status_code == 422


# ---------- create ----------
def test_create_a_new_lead(tmp_path):
    c, owner, _, _ = make(tmp_path)
    t = caller(c, owner)
    r = save(c, t, profile_url=NEW + "/?trk=abc", name="  Test   Person ", title="Head of HR", company="Initech Pvt Ltd",
             location="Pune, Maharashtra, India")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "created" and body["lead_id"]
    assert body["match"]["matched"] and body["match"]["lead_id"] == body["lead_id"] and body["match"]["name"] == "Test Person"
    assert body["match"]["has_profile_link"] is True and body["match"]["can_send"] is False
    lead = lead_by_id(c, body["lead_id"])
    assert lead["linkedin_url"] == NEW and lead["contact_name"] == "Test Person" and lead["title"] == "Head of HR"
    assert lead["company"] == "Initech Pvt Ltd" and lead["city"] == "Pune" and lead["state"] == "Maharashtra"
    assert lead["source"] == "linkedin_manual" and lead["source_id"] == "linkedin_manual" and lead["stage"] == "new"
    assert lead["owner"] == "Rita Rep" and lead["dedupe_key"] == f"linkedin:{NEW}"
    assert lead["timeline"][0]["text"] == "Saved from LinkedIn by Rita Rep"
    assert "workspace" not in lead                                   # main workspace
    # the same profile again is now a match, not a second lead
    assert match(c, t, profile_url=NEW).json()["lead_id"] == body["lead_id"]
    again = save(c, t, title="Head of HR").json()
    assert again["result"] == "unchanged" and again["lead_id"] == body["lead_id"] and again["changed"] == []
    assert sum(1 for l in c.app.state.store.read("leads") if l.get("linkedin_url") == NEW) == 1


def test_source_label_is_registered(tmp_path):
    c, owner, _, _ = make(tmp_path)
    sources = {s["id"]: s for s in c.get("/api/lead-sources", headers=bearer(owner)).json()["sources"]}
    assert sources["linkedin_manual"]["label"] == "Saved from LinkedIn"


def test_the_saved_lead_shows_in_the_leads_list(tmp_path):
    c, owner, _, _ = make(tmp_path)
    lid = save(c, pair(c, owner)["token"], company="Initech").json()["lead_id"]
    assert lid in {l["id"] for l in c.get("/api/leads", headers=bearer(owner)).json()}


# ---------- match and fill only empty fields ----------
def test_match_by_slug_fills_only_empty_fields(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    store = c.app.state.store
    store.update_lead(acme, lambda l: {**l, "title": ""})
    t = pair(c, owner)["token"]
    r = save(c, t, profile_url=URL + "?utm_source=x", name="Someone Else", title="VP People", company="Other Co", location="Delhi")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "updated" and body["lead_id"] == acme and set(body["changed"]) == {"Title", "City"}
    lead = lead_by_id(c, acme)
    assert lead["contact_name"] == "Ravi Kumar" and lead["company"] == "Acme Pvt Ltd"     # never overwritten
    assert lead["title"] == "VP People" and lead["city"] == "Delhi" and lead["email"] == "ravi@acme.com"
    assert lead["source"] == "apollo" and any(s["id"] == "linkedin_manual" for s in lead.get("sources") or [])
    assert any("Details added from LinkedIn by" in e["text"] and "Title" in e["text"] for e in lead["timeline"])
    assert body["match"]["title"] == "VP People"
    again = save(c, t, profile_url=URL, name="Ravi Kumar", title="CEO", location="Delhi").json()
    assert again["result"] == "unchanged" and lead_by_id(c, acme)["title"] == "VP People"


def test_match_by_name_and_company_adds_the_profile_link(tmp_path):
    c, owner, _, globex = make(tmp_path)
    t = pair(c, owner)["token"]
    body = save(c, t, profile_url="https://www.linkedin.com/in/meera-rao-1", name="Meera Rao", company="Globex Limited").json()
    assert body["result"] == "updated" and body["lead_id"] == globex and "LinkedIn profile" in body["changed"]
    assert lead_by_id(c, globex)["linkedin_url"] == "https://www.linkedin.com/in/meera-rao-1"


def test_same_name_and_company_with_another_profile_is_a_new_person(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    body = save(c, t, profile_url="https://www.linkedin.com/in/ravi-kumar-other", name="Ravi Kumar", company="Acme").json()
    assert body["result"] == "created" and body["lead_id"] != acme
    assert lead_by_id(c, acme)["linkedin_url"] == URL


# ---------- workspace scope ----------
def test_a_rep_limited_to_a_workspace_saves_into_it_and_cannot_touch_others(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client One"}, headers=bearer(owner)).json()
    t = caller(c, owner, workspaces=[w["id"]])
    body = save(c, t, company="Initech").json()
    assert body["result"] == "created" and lead_by_id(c, body["lead_id"])["workspace"] == w["id"]
    # Acme lives in main, which this rep does not work in: refused, nothing changed
    store = c.app.state.store
    store.update_lead(acme, lambda l: {**l, "title": ""})
    r = save(c, t, profile_url=URL, name="Ravi Kumar", title="VP")
    assert r.status_code == 409 and "workspace" in r.json()["detail"]
    assert lead_by_id(c, acme)["title"] == ""
    # an unrestricted owner token creates in main
    other = save(c, pair(c, owner)["token"], profile_url="https://www.linkedin.com/in/owner-saved").json()
    assert "workspace" not in lead_by_id(c, other["lead_id"])


# ---------- do-not-contact / suppression ----------
@pytest.mark.parametrize("change", [
    {"do_not_contact": True}, {"stage": "unsubscribed"}, {"stage": "disqualified"},
])
def test_do_not_contact_leads_are_refused(tmp_path, change):
    c, owner, acme, _ = make(tmp_path)
    store = c.app.state.store
    store.update_lead(acme, lambda l: {**l, "title": "", **change})
    r = save(c, pair(c, owner)["token"], profile_url=URL, name="Ravi Kumar", title="VP")
    assert r.status_code == 409 and "do-not-contact" in r.json()["detail"]
    assert lead_by_id(c, acme)["title"] == ""
    assert audit_rows(c)[-1]["detail"].startswith("refused")


def test_suppressed_email_is_refused(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    c.app.state.store.update("suppressed", lambda rows: [*rows, "ravi@acme.com"])
    r = save(c, pair(c, owner)["token"], profile_url=URL, name="Ravi Kumar", title="VP")
    assert r.status_code == 409 and "suppression" in r.json()["detail"]


def test_an_erased_profile_cannot_be_saved_again(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    lid = save(c, t, company="Initech").json()["lead_id"]
    assert c.post(f"/api/leads/{lid}/erase", json={"confirm": True}, headers=bearer(owner)).status_code == 200
    assert privacy.linkedin_hash(NEW) in c.app.state.store.read("suppressed_hashes")
    r = save(c, t, profile_url=NEW + "/?x=1", company="Initech")
    assert r.status_code == 409 and "erased" in r.json()["detail"]
    assert not any(l.get("linkedin_url") == NEW for l in c.app.state.store.read("leads"))
    assert "Test Person" not in audit_rows(c)[-1]["detail"]


# ---------- rate limit ----------
def test_rate_limit_per_token(tmp_path, monkeypatch):
    monkeypatch.setattr(extension_save, "SAVES_PER_HOUR", 3)
    c, owner, _, _ = make(tmp_path)
    t1, t2 = pair(c, owner, "one")["token"], pair(c, owner, "two")["token"]
    codes = [save(c, t1, profile_url=f"https://www.linkedin.com/in/p-{i}").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]
    assert "human pace" in save(c, t1, profile_url="https://www.linkedin.com/in/p-9").json()["detail"]
    assert save(c, t2, profile_url="https://www.linkedin.com/in/p-5").status_code == 200       # each token has its own window


def test_hourly_limit_window_slides():
    now = [0.0]
    lim = extension_save.HourlyLimit(clock=lambda: now[0])
    assert [lim.allow("k", 2) for _ in range(3)] == [True, True, False]
    now[0] = 3599
    assert not lim.allow("k", 2)
    now[0] = 3600
    assert lim.allow("k", 2) and lim.allow("other", 2)


# ---------- audit ----------
def test_every_save_is_audited(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = caller(c, owner)
    created = save(c, t, company="Initech").json()
    save(c, t, company="Initech")
    rows = audit_rows(c)
    assert len(rows) == 2 and rows[0]["by"] == "Rita Rep" and rows[0]["by_email"] == "rep@x.com"
    assert rows[0]["detail"].startswith(f"created lead {created['lead_id']}") and rows[1]["detail"].startswith(f"unchanged lead {created['lead_id']}")
    trail = c.get("/api/audit", params={"action": "extension"}, headers=bearer(owner))
    assert trail.status_code == 200, trail.text
    assert trail.json()["total"] == 2 and trail.json()["actions"]["extension.save_profile"]


# ---------- token scope ----------
def test_only_an_extension_token_may_save(tmp_path):
    c, owner, _, _ = make(tmp_path)
    r = c.post(PATH, json={"profile_url": NEW, "name": "Test Person"}, headers=bearer(owner))
    assert r.status_code == 403
    assert c.post(PATH, json={"profile_url": NEW, "name": "Test Person"}).status_code == 401


def test_a_viewer_token_cannot_save(tmp_path):
    c, owner, _, _ = make(tmp_path)
    t = caller(c, owner)
    c.put("/api/users/rep@x.com", json={"role": "viewer"}, headers=bearer(owner))
    assert save(c, t).status_code == 403
    assert not any(l.get("linkedin_url") == NEW for l in c.app.state.store.read("leads"))


def test_the_extension_token_still_cannot_reach_other_routes(tmp_path):
    c, owner, _, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    assert c.get(PATH, headers=bearer(t)).status_code == 403                  # GET is not allowed
    assert c.post("/api/extension/save-profile/x", json={}, headers=bearer(t)).status_code == 403
    assert c.post("/api/leads", json={"company": "x"}, headers=bearer(t)).status_code == 403


def test_owner_workspace_list_does_not_limit_their_token(tmp_path):
    c, owner, _, _ = make(tmp_path)
    p = pair(c, owner)
    store = c.app.state.store
    from app import extension_api as ext
    principal = ext.authenticate(store, ADMIN, p["token"])
    assert principal["role"] == "caller" and principal["workspaces"] == []
