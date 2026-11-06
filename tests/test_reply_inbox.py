"""The reply inbox: replies from every channel, scored, best first."""
from datetime import datetime, timezone

from app.reply_inbox import build_inbox, parse_reply, score_reply
from tests.flow_helpers import lead, make_client


def test_parse_reply_reads_the_timeline_line():
    p = parse_reply({"kind": "reply", "text": "[interested] (linkedin, agent) Yes, send details"})
    assert p == {"label": "interested", "channel": "linkedin", "source": "agent", "text": "Yes, send details"}
    assert parse_reply({"kind": "note", "text": "[interested] (email, x) hi"}) is None
    assert parse_reply({"kind": "reply", "text": "no brackets here"}) is None


def test_scores_order_by_worth():
    yes = score_reply("interested", "yes please call me", 80)
    ask = score_reply("question", "what is the price?", 0)
    later = score_reply("not_now", "maybe after diwali", 0)
    assert yes > ask > later
    assert score_reply("bounce", "x", 100) == 0 and score_reply("unsubscribe", "stop", 100) == 0
    assert score_reply("not_interested", "no thanks", 100) <= 8
    assert 0 <= score_reply("interested", "price demo call 9810012345", 100) <= 99


def test_hedging_lowers_but_never_below_floor():
    assert score_reply("interested", "maybe, not sure", 0) >= 78


def test_inbox_through_the_api_and_filters(tmp_path):
    c = make_client(tmp_path)
    a, b = lead(c, "Acme"), lead(c, "Beta")
    assert c.post(f"/api/leads/{a['id']}/reply", json={"text": "Yes, please call me tomorrow"}).status_code == 200
    assert c.post(f"/api/leads/{b['id']}/reply", json={"text": "No thanks, not interested"}).status_code == 200
    data = c.get("/api/reply-inbox").json()
    assert data["total"] == 2 and data["replies"][0]["company"] == "Acme" and data["replies"][0]["label"] == "interested"
    assert data["replies"][0]["needs_action"] and not data["replies"][-1]["needs_action"]
    assert data["needs_action"] == 1 and set(data["counts"]) == {"interested", "not_interested"}
    assert [r["company"] for r in c.get("/api/reply-inbox?label=not_interested").json()["replies"]] == ["Beta"]
    assert [r["company"] for r in c.get("/api/reply-inbox?only_action=true").json()["replies"]] == ["Acme"]
    assert c.get("/api/reply-inbox?label=bogus").json()["total"] == 2          # unknown label is ignored, not an error


def test_only_newest_reply_needs_action():
    def reply(at, text): return {"at": at, "kind": "reply", "text": text}
    leads = [{"id": "1", "company": "X", "stage": "contacted", "timeline": [
        reply("2026-11-01T10:00:00+00:00", "[question] (email, pasted) how much?"),
        reply("2026-11-02T10:00:00+00:00", "[not_interested] (email, pasted) no thanks")]}]
    out = build_inbox(leads, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    old, new = sorted(out["replies"], key=lambda r: r["at"])
    assert not old["needs_action"] and not new["needs_action"] and out["needs_action"] == 0 and old["age_hours"] == 38.0


def test_viewer_may_read_inbox(tmp_path):
    c = make_client(tmp_path, csv=None)
    assert c.get("/api/reply-inbox").status_code == 200


def test_replies_can_be_assigned_to_a_login(tmp_path):
    # QA sweep: callers added as logins could own leads but were missing from the Replies "Assign to" list.
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.flow_helpers import CSV
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    c.headers["Authorization"] = "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    assert c.post("/api/users", json={"email": "priya@example.in", "name": "Priya Verma", "role": "caller",
                                      "password": "caller-password-1"}).status_code in (200, 201)
    a = lead(c, "Acme")
    c.post(f"/api/leads/{a['id']}/reply", json={"text": "Can we talk on Tuesday at 3pm?"})
    data = c.get("/api/reply-inbox").json()
    assert "Priya Verma" in data["team"]
    key = data["replies"][0]["key"]
    r = c.post("/api/reply-inbox/action", json={"key": key, "action": "assign", "assignee": "Priya Verma"})
    assert r.status_code == 200 and r.json()["state"]["assignee"] == "Priya Verma"
    assert c.post("/api/reply-inbox/action", json={"key": key, "action": "assign", "assignee": "Nobody Known"}).status_code == 400


def test_thread_shows_the_reply_words_not_the_internal_tag(tmp_path):
    # QA sweep: the Replies thread and the activity list showed "[not_now] (email, pasted) Hi …".
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    c.post(f"/api/leads/{a['id']}/reply", json={"text": "Abhi nahi, next quarter mein baat karte hain."})
    msg = [m for m in c.get(f"/api/leads/{a['id']}/thread").json()["messages"] if m["direction"] == "in"][-1]
    assert msg["body"] == "Abhi nahi, next quarter mein baat karte hain." and msg["status"] == "read as not now"
    assert msg["title"] == "Reply (email)"
