"""Deliverability report (app/deliverability.py, GET /api/deliverability): rates, windows, groups, no addresses, roles and scoping."""
import json
from datetime import datetime, timedelta, timezone

from app import deliverability
from tests.ownership_helpers import rig

NOW = datetime.now(timezone.utc)


def at(days_ago, hour=1):
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def seed(r):
    """In 30 days: Acme (apollo) 4 sent + 1 failed, 2 bounced; Beta (apollo) 2 sent; Cora (csv) 3 sent + one 40 days old, 1 bounce + 1 complaint;
    other workspace 2 sent, 1 bounce. A 60-day-old Beta bounce is outside the window."""
    ids = {name: r.lead[name]["id"] for name in ("Acme", "Beta Ltd", "Cora Inc")}
    ids["Other"] = r.other["id"]
    r.set("Cora Inc", source="csv_upload", email_check={"status": "valid", "provider": "zerobounce", "email": "tej@cora.io"})
    r.set("Acme", source="apollo", email_check={"status": "risky", "provider": "builtin", "email": "ravi@acme.com"})
    r.set("Beta Ltd", source="apollo")
    r.set("Other WS Co", id=ids["Other"], source="meta_ads")
    def msg(name, to, step, days_ago, status="sent"):
        return {"lead_id": ids[name], "to": to, "step": step, "status": status, "sent_at": at(days_ago, 1)}
    outbox = [msg("Acme", "ravi@acme.com", "Email 1", 2), msg("Acme", "ravi@acme.com", "Email 2", 1), msg("Acme", "ravi@acme.com", "Email 1", 3),
              msg("Acme", "ravi@acme.com", "Email 1", 4, status="failed"), msg("Acme", "ravi@acme.com", "Email 1", 5),
              msg("Beta Ltd", "sita@beta.in", "Email 1", 2), msg("Beta Ltd", "sita@beta.in", "Email 2", 2),
              msg("Cora Inc", "tej@cora.io", "Email 1", 2), msg("Cora Inc", "tej@cora.io", "Email 1", 3), msg("Cora Inc", "tej@cora.io", "Email 2", 3), msg("Cora Inc", "tej@cora.io", "Email 1", 40),
              msg("Other", "zed@other.com", "Email 1", 2), msg("Other", "zed@other.com", "Email 1", 3)]
    events = [{"key": "b1", "type": "bounce", "email": "ravi@acme.com", "at": at(1, 5)}, {"key": "b2", "type": "bounce", "email": "ravi@acme.com", "at": at(2, 5)},
              {"key": "b3", "type": "bounce", "email": "tej@cora.io", "at": at(2, 6)}, {"key": "c1", "type": "complaint", "email": "tej@cora.io", "at": at(2, 7)},
              {"key": "d1", "type": "delivered", "email": "sita@beta.in", "at": at(2, 2)}, {"key": "b4", "type": "bounce", "email": "zed@other.com", "at": at(2, 5)},
              {"key": "old", "type": "bounce", "email": "sita@beta.in", "at": at(60)}]
    r.store.update("outbox", lambda rows: [*rows, *outbox])
    r.store.update("delivery_events", lambda rows: [*rows, *events])
    return ids


def get(r, who="owner", path="/api/deliverability", **headers):
    return r.c.get(path, headers={**r.h[who], **headers})


def row(rows, key):
    return next(x for x in rows if x["key"] == key)


def test_totals_rates_and_window(tmp_path):
    r = rig(tmp_path)
    seed(r)
    body = get(r, "owner", "/api/deliverability?days=30", **{"X-Workspace": "main"}).json()
    assert body["days"] == 30 and (body["sent"], body["bounced"], body["complaints"], body["delivered"]) == (9, 3, 1, 1)   # 'old' (60 d), failed and the 40-day-old send are outside
    assert body["bounce_rate"] == round(3 / 9, 4) and body["complaint_rate"] == round(1 / 9, 4)
    assert body["verdict"]["level"] == "bad" and "pauses" in body["verdict"]["text"]
    wide = get(r, "owner", "/api/deliverability?days=90", **{"X-Workspace": "main"}).json()
    assert wide["sent"] == 10 and wide["bounced"] == 4                                                      # now the 40-day send and the 60-day bounce count
    short = get(r, "owner", "/api/deliverability?days=1", **{"X-Workspace": "main"}).json()
    assert short["days"] == 1 and len(short["by_day"]) == 1


def test_by_source_domain_step_check_type(tmp_path):
    r = rig(tmp_path)
    seed(r)
    b = get(r, "owner", **{"X-Workspace": "main"}).json()
    apollo, csv = row(b["by_source"], "apollo"), row(b["by_source"], "csv_upload")
    assert (apollo["sent"], apollo["bounced"]) == (6, 2) and (csv["sent"], csv["bounced"], csv["complaints"]) == (3, 1, 1)
    assert apollo["bounce_rate"] == round(2 / 6, 4)
    assert b["by_source"][0]["key"] in ("csv_upload", "apollo") and b["by_source"][0]["bounce_rate"] >= b["by_source"][-1]["bounce_rate"]   # worst first
    assert row(b["by_domain"], "acme.com")["bounced"] == 2 and row(b["by_domain"], "acme.com")["sent"] == 4
    step1, step2 = row(b["by_step"], "Email 1"), row(b["by_step"], "Email 2")
    assert step2["bounced"] == 1 and step1["bounced"] + step2["bounced"] == 3                              # each bounce goes to the last email sent before it
    assert row(b["by_check"], "Valid (zerobounce)")["sent"] == 3 and row(b["by_check"], "Valid (zerobounce)")["bounced"] == 1
    assert row(b["by_check"], "Risky (builtin)")["bounced"] == 2 and row(b["by_check"], "Not checked")["sent"] == 2
    assert row(b["by_address_type"], "Named person")["sent"] == 9
    assert row(b["by_mailbox_type"], "Company domain")["sent"] == 9


def test_by_day_trend(tmp_path):
    r = rig(tmp_path)
    seed(r)
    b = get(r, "owner", "/api/deliverability?days=7", **{"X-Workspace": "main"}).json()
    days = {d["day"]: d for d in b["by_day"]}
    assert len(b["by_day"]) == 7 and b["by_day"][-1]["day"] == NOW.date().isoformat()
    assert days[at(2)[:10]]["sent"] == 4 and days[at(2)[:10]]["bounced"] == 2 and days[at(1)[:10]]["sent"] == 1


def test_no_addresses_or_names_and_only_top_domains(tmp_path):
    r = rig(tmp_path)
    seed(r)
    for who, ws in (("owner", "main"), ("adm", "main")):
        text = json.dumps(get(r, who, **{"X-Workspace": ws}).json())
        assert "@" not in text and "Ravi" not in text and "Acme" not in text and "Sita" not in text
    many = [{"lead_id": r.lead["Acme"]["id"], "to": f"x@d{i}.in", "step": "Email 1", "status": "sent", "sent_at": at(1)} for i in range(30)]
    r.store.update("outbox", lambda rows: [*rows, *many])
    assert len(get(r, "owner", **{"X-Workspace": "main"}).json()["by_domain"]) == deliverability.TOP_DOMAINS


def test_sending_state_and_thresholds(tmp_path):
    r = rig(tmp_path)
    seed(r)
    s = get(r, "owner", **{"X-Workspace": "main"}).json()["sending"]
    assert s == {"paused": False, "reason": "", "since": "", "pause_above_bounce_rate": 0.02, "pause_above_complaint_rate": 0.003, "min_sends_for_pause": 50}
    r.store.update("settings", lambda x: {**x, "sending_paused": {"reason": "too many bounces", "at": at(0)}})
    s = get(r, "owner", **{"X-Workspace": "main"}).json()["sending"]
    assert s["paused"] is True and s["reason"] == "too many bounces"


def test_verdict_levels():
    assert deliverability.verdict(0, 0, 0)["level"] == "none"
    assert deliverability.verdict(500, 0.004, 0)["level"] == "good"
    assert deliverability.verdict(500, 0.015, 0)["level"] == "warn"
    assert deliverability.verdict(500, 0.03, 0)["level"] == "bad"
    assert deliverability.verdict(500, 0.001, 0.01)["level"] == "bad"


def test_roles_and_workspace_scoping(tmp_path):
    r = rig(tmp_path)
    seed(r)
    assert get(r, "priya").status_code == 403 and get(r, "view").status_code == 403 and get(r, "ravi").status_code == 403
    main = get(r, "adm").json()
    assert main["sent"] == 9 and main["bounced"] == 3                                                      # a Main admin never sees the other workspace
    other = get(r, "adm2", **{"X-Workspace": r.w2}).json()
    assert (other["sent"], other["bounced"]) == (2, 1) and [x["key"] for x in other["by_source"]] == ["meta_ads"]
    assert get(r, "owner", **{"X-Workspace": r.w2}).json()["sent"] == 2


def test_client_account_mail_is_not_in_the_mas_callnet_report(tmp_path):
    r = rig(tmp_path)
    seed(r)
    from app import accounts
    acct = accounts.create(r.store, "Client Co", "test")
    r.store.update("settings", lambda s: {**s, "workspaces": [{**w, "account_id": acct["id"]} if w.get("id") == r.w2 else w for w in s.get("workspaces", [])]})
    assert accounts.ws_account_map(r.store)[r.w2] == acct["id"]
    owner_all = get(r, "owner").json()                                                                      # Mas Callnet's own report leaves the client's mail out
    assert owner_all["sent"] == 9 and owner_all["bounced"] == 3


def test_empty_store_is_not_an_error(tmp_path):
    r = rig(tmp_path)
    b = get(r, "owner").json()
    assert b["sent"] == 0 and b["bounce_rate"] == 0 and b["verdict"]["level"] == "none" and b["by_source"] == []


def test_bad_days_is_rejected(tmp_path):
    r = rig(tmp_path)
    assert get(r, "owner", "/api/deliverability?days=0").status_code == 422
    assert get(r, "owner", "/api/deliverability?days=9999").status_code == 422
