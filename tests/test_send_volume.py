"""Unconfirmed addresses at a lower volume (app/send_volume.py): a share of each mailbox's daily sends (default 100 = off; 20 recommended),
0 and 100, overflow waits for a later day (never dropped), confirmed addresses are never held, practice is not limited."""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import send_volume
from app.main import create_app
from app.sequence_runner import run_due
from tests.flow_helpers import connect_email, go_live
from tests.test_email_gate import T0, Sender, csv_for

VALID = {"status": "valid", "provider": "zerobounce"}


def make(tmp_path, n=6, confirmed=0, share=None):
    """n leads (p0@co0.in ...); the first `confirmed` of them have a verifier's valid check. Warm-up: 10 a day, so 20% = 2."""
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": csv_for(n), "source": "apollo"})
    store = c.app.state.store
    connect_email(c)
    go_live(c)
    store.update("settings", lambda s: {**s, "per_run_cap": 50})
    if share is not None:
        send_volume.save_share(store, share)
    for lead in c.get("/api/leads").json():
        i = int(lead["email"][1:].split("@")[0])
        if i < confirmed:
            store.update_lead(lead["id"], lambda l: {**l, "email_check": {**VALID, "email": l["email"]}})
        assert c.post(f"/api/leads/{lead['id']}/enroll").status_code == 200
    return c, store


def sent_to(store):
    return [m["to"] for m in store.read("outbox") if m.get("status") == "sent"]


def test_allowance_rules():
    assert send_volume.allowance(10, 20) == 2 and send_volume.allowance(10, 0) == 0 and send_volume.allowance(10, 100) == 10
    assert send_volume.allowance(3, 20) == 1          # a share above 0 lets at least one through
    assert send_volume.allowance(0, 50) == 0 and send_volume.allowance(200, 15) == 30


def test_share_of_20_percent_and_the_rest_wait_for_the_next_day(tmp_path):
    c, store = make(tmp_path, share=20)
    assert send_volume.share_of(store) == 20
    out = run_due(store, T0, "", Sender())
    assert out["sent"] == 2 and out["waiting_unconfirmed"] == 4 and out["held"] == 4
    assert run_due(store, T0 + timedelta(minutes=30), "", Sender())["sent"] == 0      # same day: the share is used up
    waiting = [l for l in store.read("leads") if not l["sequence"].get("sent_days")]
    assert len(waiting) == 4 and all(l["sequence"]["blocked"] == send_volume.NOTE and not l["sequence"]["stopped"] for l in waiting)
    assert all(m.get("email_state") == "domain_ready" for m in store.read("outbox"))
    nxt = run_due(store, T0 + timedelta(days=1), "", Sender())                        # Wednesday: two more, nothing dropped
    assert nxt["sent"] == 2 and len(sent_to(store)) == 4
    after = [l for l in store.read("leads") if l["sequence"].get("sent_days")]
    assert all("blocked" not in l["sequence"] for l in after)                         # the note goes once the email went


def test_confirmed_addresses_are_never_held(tmp_path):
    c, store = make(tmp_path, n=6, confirmed=4, share=0)
    out = run_due(store, T0, "", Sender())
    assert out["sent"] == 4 and out["waiting_unconfirmed"] == 2
    assert sorted(sent_to(store)) == [f"p{i}@co{i}.in" for i in range(4)]
    assert all(m["email_state"] == "confirmed" for m in store.read("outbox"))


def test_zero_never_sends_to_unconfirmed(tmp_path):
    c, store = make(tmp_path, n=3, share=0)
    assert run_due(store, T0, "", Sender())["sent"] == 0
    assert run_due(store, T0 + timedelta(days=1), "", Sender())["sent"] == 0
    assert all(not l["sequence"]["stopped"] for l in store.read("leads"))            # still waiting, not dropped


def test_hundred_is_no_limit(tmp_path):
    c, store = make(tmp_path, n=6, share=100)
    out = run_due(store, T0, "", Sender())
    assert out["sent"] == 6 and "waiting_unconfirmed" not in out


def test_confirmed_sends_do_not_use_the_share(tmp_path):
    c, store = make(tmp_path, n=6, confirmed=3, share=20)          # 3 confirmed + 3 unconfirmed; share 20% of 10 = 2 unconfirmed
    out = run_due(store, T0, "", Sender())
    assert out["sent"] == 5 and out["waiting_unconfirmed"] == 1


def test_practice_runs_are_not_limited(tmp_path):
    c, store = make(tmp_path, n=6, share=0)
    store.update("settings", lambda s: {**s, "live_sending": False})
    assert run_due(store, T0, "", Sender())["sent"] == 6


def test_owner_sets_the_share_and_it_is_shown(tmp_path):
    c, store = make(tmp_path, n=1)
    assert c.get("/api/deliverability").json()["volume"] == {"domain_ready_share": 100, "default_share": 100}
    r = c.put("/api/deliverability/volume", json={"domain_ready_share": 35})
    assert r.status_code == 200 and r.json()["domain_ready_share"] == 35 and send_volume.share_of(store) == 35
    assert c.put("/api/deliverability/volume", json={"domain_ready_share": 101}).status_code == 422
    assert any(a.get("action") == "deliverability.volume" for a in store.read("audit"))


def test_default_is_off_so_deploying_changes_nothing(tmp_path):
    c, store = make(tmp_path)
    assert send_volume.DEFAULT_SHARE == 100 and send_volume.share_of(store) == 100
