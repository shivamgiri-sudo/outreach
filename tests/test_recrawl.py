"""Scheduled website re-crawl and change timeline (app/recrawl.py): diffing, batch cap, closed / inactive leads skipped,
the alert only on "Started hiring", settings and routes. No network: the site fetcher is a fake. Names are made up."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import recrawl
from app.main import create_app
from app.store import Store

NOW = datetime(2026, 12, 20, 6, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=40)).isoformat(timespec="seconds")


def page(jobs=(), phone="", social=()):
    org = {"@type": "Organization", "name": "Acme", "telephone": phone, "sameAs": list(social)}
    posts = "".join(f'<script type="application/ld+json">{json.dumps({"@type": "JobPosting", "title": t, "datePosted": "2026-12-01", "hiringOrganization": {"@type": "Organization", "name": "Acme"}, "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Pune"}}})}</script>'
                    for t in jobs)
    return f'<html><head><script type="application/ld+json">{json.dumps(org)}</script>{posts}</head><body></body></html>'


class Site:
    def __init__(self, html_by_domain):
        self.html, self.urls = html_by_domain, []

    def __call__(self, url):
        self.urls.append(url)
        domain = url.split("/")[2]
        if url == f"https://{domain}/" and domain in self.html:
            return self.html[domain]
        raise OSError("not found")


def lead(i, domain, stage="contacted", snap=None, seq=True, owner="Ravi"):
    out = {"id": f"L{i}", "company": f"Co{i}", "domain": domain, "stage": stage, "owner": owner, "timeline": [],
           "contact_name": "", "email": f"x@{domain}"}
    if seq:
        out["sequence"] = {"enrolled_at": OLD, "sent_days": [0], "stopped": None}
    if snap is not None:
        out["site_snapshot"] = {"at": OLD, **snap}
    return out


def make(tmp_path, leads):
    store = Store(tmp_path / "s.json")
    store.update("leads", lambda _: leads)
    return store


def lines(store, lead_id):
    return [e["text"] for e in next(l for l in store.read("leads") if l["id"] == lead_id)["timeline"] if e["kind"] in ("jobs", "enrichment") and not e["text"].startswith("Filled from")]


def bell(store):
    return [n for n in store.read("notifications") if n.get("kind") == "alert:started_hiring"]


def test_started_hiring_line_and_alert_to_the_owner(tmp_path):
    store = make(tmp_path, [lead(1, "acme.example", snap={"roles": 0, "titles": [], "phone": "", "social": []})])
    out = recrawl.run_once(store, Site({"acme.example": page(["Team Lead", "Agent"])}), NOW)
    assert out["domains"] == 1 and out["started"] == 1
    assert lines(store, "L1") == ["Started hiring (2 roles)"]
    assert [n["to"] for n in bell(store)] == ["Ravi"]
    assert next(l for l in store.read("leads") if l["id"] == "L1")["site_snapshot"]["roles"] == 2


def test_stopped_new_roles_phone_and_social(tmp_path):
    store = make(tmp_path, [
        lead(1, "a.example", snap={"roles": 2, "titles": ["Agent"], "phone": "", "social": []}),
        lead(2, "b.example", snap={"roles": 1, "titles": ["Agent"], "phone": "+91 98765 43210", "social": ["https://x.com/bco"]})])
    site = Site({"a.example": page([]),
                 "b.example": page(["Agent", "Quality Analyst"], phone="+91 91234 56789", social=["https://x.com/bco", "https://www.instagram.com/bco"])})
    recrawl.run_once(store, site, NOW)
    assert lines(store, "L1") == ["Stopped hiring"]
    assert lines(store, "L2") == ["New roles: Quality Analyst", "Phone on website changed", "New social link: https://www.instagram.com/bco"]
    assert bell(store) == []                                                   # alerts only for "Started hiring"


def test_no_change_means_no_line_and_first_read_only_stores(tmp_path):
    same = {"roles": 1, "titles": ["Agent"], "phone": "+91 98765 43210", "social": []}
    store = make(tmp_path, [lead(1, "a.example", snap=same), lead(2, "b.example")])
    recrawl.run_once(store, Site({"a.example": page(["Agent"], phone="098765 43210"), "b.example": page(["Agent"])}), NOW)
    assert lines(store, "L1") == []
    assert all(not t.startswith("Started") for t in lines(store, "L2"))        # first read: snapshot only, no change lines
    assert next(l for l in store.read("leads") if l["id"] == "L2")["site_snapshot"]["roles"] == 1
    assert bell(store) == []


def test_unreadable_site_keeps_snapshot_and_waits(tmp_path):
    store = make(tmp_path, [lead(1, "down.example", snap={"roles": 3, "titles": ["Agent"], "phone": "", "social": []})])
    out = recrawl.run_once(store, Site({}), NOW)
    snap = store.read("leads")[0]["site_snapshot"]
    assert out["errors"] == 1 and snap["roles"] == 3 and snap["tried_at"] == NOW.isoformat(timespec="seconds")
    assert lines(store, "L1") == [] and recrawl.pick(store, NOW + timedelta(hours=2)) == []


def test_batch_cap_closed_inactive_and_recent_skipped(tmp_path):
    leads = [lead(i, f"d{i}.example") for i in range(25)]
    leads += [lead(90, "closed.example", stage="disqualified"), lead(91, "idle.example", seq=False),
              {**lead(92, "fresh.example"), "hiring_signal": {"checked_at": (NOW - timedelta(days=3)).isoformat()}}]
    store = make(tmp_path, leads)
    work = recrawl.pick(store, NOW, limit=500)
    domains = [d for d, _ in work]
    assert len(work) == recrawl.BATCH
    assert not {"closed.example", "idle.example", "fresh.example"} & set(domains)
    store.update("lists", lambda _: [{"id": "li1", "lead_ids": ["L91"]}])          # on a list: active
    store.update("leads", lambda rows: [r for r in rows if r["id"] in ("L90", "L91")])
    assert [d for d, _ in recrawl.pick(store, NOW)] == ["idle.example"]


def test_switch_off_and_hourly_tick(tmp_path):
    store = make(tmp_path, [lead(1, "a.example", snap={"roles": 0, "titles": [], "phone": "", "social": []})])
    site = Site({"a.example": page(["Agent"])})
    assert recrawl.tick(store, site, NOW)["domains"] == 1
    assert recrawl.tick(store, site, NOW + timedelta(minutes=30)) is None          # at most once an hour
    store.update("settings", lambda s: {**s, "recrawl": {"enabled": False, "days": 30}})
    assert recrawl.tick(store, site, NOW + timedelta(hours=3)) is None


def test_routes_owner_only(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    got = c.get("/api/recrawl", headers=ho).json()
    assert (got["enabled"], got["days"], got["batch"]) == (True, 30, 20)
    assert c.put("/api/recrawl", json={"enabled": False, "days": 45}, headers=ho).json()["days"] == 45
    assert c.put("/api/recrawl", json={"enabled": True, "days": 2}, headers=ho).status_code == 422
    c.post("/api/users", json={"email": "raj@example.com", "name": "Raj", "role": "caller", "password": "long-password-1"}, headers=ho)
    hc = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "raj@example.com", "password": "long-password-1"}).json()["token"]}
    assert c.get("/api/recrawl", headers=hc).status_code == 403
    assert c.put("/api/recrawl", json={"enabled": True, "days": 30}, headers=hc).status_code == 403
