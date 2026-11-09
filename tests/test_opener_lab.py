"""Connection-note lab: rule scores, Claude candidates (faked), saving A/B, and render using the saved notes."""
from app import linkedin_seq as li, opener_lab as lab
from tests.flow_helpers import lead, make_client, patch_lead, store_of


def test_score_rules():
    good = lab.score_note("Hi {first_name}, I talk to HR leaders about what slows hiring down. Would be glad to connect and swap notes on building teams.")
    assert good["ok"] and good["score"] >= 75
    pitch = lab.score_note("Hi {first_name}, book a call for a free trial and see our pricing at https://x.com!!!")
    assert pitch["score"] < good["score"] and any("pitch" in r for r in pitch["reasons"])
    assert not lab.score_note("x" * 290 + " {first_name}")["ok"]
    assert not lab.score_note("Hi {name}, hello")["ok"]


def test_candidates_from_claude_and_fallback(tmp_path):
    c = make_client(tmp_path)
    store = store_of(c)
    assert lab.candidates(store)["source"] == "built_in"
    store.update("integrations", lambda i: {**i, "llm": {"mode": "api", "values": {"provider": "anthropic", "api_key": "k"}}})
    fake = lambda url, headers, body: {"content": [{"type": "text", "text": '{"notes": ["Hi {first_name}, keen to connect with people who build hiring teams.", "Buy now! http://x"]}'}]}
    out = lab.candidates(store, "CHROs", 2, transport=fake)
    assert out["source"] == "claude" and out["candidates"][0]["text"].startswith("Hi {first_name}") and out["candidates"][0]["score"] > out["candidates"][1]["score"]
    broken = lambda url, headers, body: {"content": [{"type": "text", "text": "no json"}]}
    assert lab.candidates(store, "", 2, transport=broken)["source"] == "built_in"


def test_saved_notes_are_used_for_new_connection_requests(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/linkedin/variants", json={"A": "Hi {first_name}, saved note A about hiring teams, happy to connect."})
    assert r.status_code == 200 and r.json()["notes"]["A"]["text"].startswith("Hi {first_name}, saved")
    assert c.put("/api/linkedin/variants", json={"B": "Hi {foo}"}).status_code == 400
    settings = store_of(c).read("settings")
    text = li.render(li.STEPS[0], {"contact_name": "Asha Rao", "linkedin_seq": {"variant": "A"}}, settings)
    assert text == "Hi Asha, saved note A about hiring teams, happy to connect."
    assert li.render(li.STEPS[0], {"contact_name": "Asha Rao", "linkedin_seq": {"variant": "B"}}, settings) == li.render(li.STEPS[0], {"contact_name": "Asha Rao"}, {})  # no saved B: the approved note
    assert c.post("/api/linkedin/variants/generate", json={}).json()["source"] == "built_in"
    assert c.post("/api/linkedin/variants/score", json={"text": "Hi {first_name}"}).json()["ok"] is True
