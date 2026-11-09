"""Phase 5, item 5 and 6: abuse alerts to the super admins (bell + email, once each), the automatic sending hold of an
account with a bad bounce or complaint rate, and the constants that decide them (app/abuse_alerts.py)."""
from datetime import datetime, timedelta, timezone

from app import abuse_alerts as ab, accounts, inbound, login_log, signup
from tests.limits_helpers import BOSS, audit_rows, bearer, lead_of, make_rig, mails_to
from tests.signup_helpers import SUPER, make as signup_make, signed_up

IST = timezone(timedelta(hours=5, minutes=30))


def now():
    return datetime.now(timezone.utc)


def pending(store, n, domain=None, ip=None, prefix="u"):
    out = []
    for i in range(n):
        email = f"{prefix}{i}@{domain or f'co{prefix}{i}.example'}"
        made = signup.create_pending(store, f"{prefix.upper()} Trading {i}", f"Person {i}", email, "", {"ip": ip or f"198.51.100.{i + 1}", "terms": "x"},
                                     password_hash="x", via="email")
        out.append(made["account"]["id"])
    return out


def kinds(store):
    return [a["kind"] for a in ab.feed(store)]


def boss_mails(store, text, who=BOSS):
    return [m for m in mails_to(store, who) if text in m["subject"]]


# ---------------------------------------------------------------- the constants
def test_thresholds_are_constants_in_one_module():
    assert (ab.SIGNUPS_PER_HOUR, ab.SIGNUPS_PER_SOURCE_HOUR) == (5, 3)
    assert (ab.BOUNCE_HOLD_RATE, ab.COMPLAINT_HOLD_RATE, ab.RATE_DAYS) == (0.02, 0.003, 7)
    assert (ab.SPIKE_FACTOR, ab.SPIKE_DAYS) == (5, 14)
    assert ab.FAILED_LOGINS == 5 and ab.FAILED_LOGIN_MINUTES == 15
    assert ab.BOUNCE_HOLD_RATE == inbound.BOUNCE_PAUSE_RATE and ab.COMPLAINT_HOLD_RATE == inbound.COMPLAINT_PAUSE_RATE   # same as the platform's own pause


# ---------------------------------------------------------------- sign-up surge
def test_six_signups_in_an_hour_alert_once(tmp_path, monkeypatch):
    c, ha, hs, outbox = signup_make(tmp_path, monkeypatch)
    store = c.app.state.store
    pending(store, 5)
    assert "signup_surge" not in kinds(store)                          # five is allowed
    pending(store, 1, prefix="v")
    assert kinds(store).count("signup_surge") == 1 and boss_mails(store, "Sign-up surge: 6 in an hour", SUPER)
    pending(store, 1, prefix="w")                                      # the same hour: not alerted again
    assert kinds(store).count("signup_surge") == 1
    assert [n["to"] for n in store.read("notifications") if "Sign-up surge" in n["title"]] == ["Boss"]
    assert audit_rows(store, "abuse.signup_surge")
    assert c.get("/api/console/accounts", headers=hs).json()["alerts"][0]["kind"] == "signup_surge"


def test_four_signups_from_one_address_or_one_company_domain_alert(tmp_path, monkeypatch):
    c, ha, hs, outbox = signup_make(tmp_path, monkeypatch)
    store = c.app.state.store
    pending(store, 3, domain="acme-freight.example", ip="198.51.100.77")
    assert not kinds(store)
    pending(store, 1, domain="acme-freight.example", ip="198.51.100.77", prefix="z")
    titles = [a["title"] for a in ab.feed(store)]
    assert any("IP address: 198.51.100.77" in t for t in titles) and any("email domain: acme-freight.example" in t for t in titles)
    free = tmp_path / "free"
    free.mkdir()
    c2, _, _, _ = signup_make(free, monkeypatch)
    pending(c2.app.state.store, 4, domain="gmail.com")                  # a shared free-mail domain says nothing
    assert not [a for a in ab.feed(c2.app.state.store) if "email domain" in a["title"]]


# ---------------------------------------------------------------- bounce and complaint rate: the automatic hold
def client_with_traffic(tmp_path, monkeypatch, sent=60, bounces=0, complaints=0):
    c, ha, hs, outbox = signup_make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    c.post(f"/api/console/accounts/{aid}/approve", headers=hs)
    c.put(f"/api/accounts/{aid}", json={"integrations_granted": ["sending_domain"]}, headers=hs)
    store = c.app.state.store
    space = next(w["id"] for w in store.read("settings")["workspaces"] if w["account_id"] == aid)
    at = now().isoformat(timespec="seconds")
    leads = [{"id": f"L{i}", "workspace": space, "stage": "contacted", "timeline": [], "email": f"p{i}@x{i}.example", "dedupe_key": f"email:p{i}@x{i}.example"}
             for i in range(sent)]
    store.update("leads", lambda rows: [*rows, *leads])
    store.update("outbox", lambda rows: [*rows, *[{"lead_id": f"L{i}", "status": "sent", "sent_at": at, "account_id": aid,
                                                   "sent_at_ist_date": now().astimezone(IST).date().isoformat()} for i in range(sent)]])
    events = [{"key": f"b{i}", "type": "bounce", "email": f"p{i}@x{i}.example", "at": at} for i in range(bounces)]
    events += [{"key": f"c{i}", "type": "complaint", "email": f"p{i + 30}@x{i + 30}.example", "at": at} for i in range(complaints)]
    store.update("delivery_events", lambda rows: [*rows, *events])
    token = c.post("/api/login", json={"email": email, "password": "delta-long-password-1"}).json()["token"]
    return c, hs, bearer(token), aid, store


def test_bounce_rate_above_2_percent_holds_the_account_and_tells_the_super_admins(tmp_path, monkeypatch):
    c, hs, hb, aid, store = client_with_traffic(tmp_path, monkeypatch, sent=60, bounces=3)         # 5% over 7 days
    assert ab.check_deliverability(store) == 1 and ab.check_deliverability(store) == 0              # held once
    row = accounts.find(store, aid)
    assert row["auto_hold"]["kind"] == "bounce_rate" and "3 bounces" in row["auto_hold"]["reason"]
    assert accounts.hold_reason(store, aid, "email").startswith("Sending is held:") and "3 bounces" in accounts.hold_reason(store, aid, "whatsapp")
    assert kinds(store) == ["bounce_rate"] and boss_mails(store, "sending held", SUPER) and len(audit_rows(store, "account.auto_hold", aid)) == 1
    lead = c.get("/api/leads", headers=hb).json()[0]["id"]
    mail = c.post(f"/api/leads/{lead}/email-compose", json={"subject": "Hello there", "body": "A short note."}, headers=hb)
    assert mail.status_code == 400 and "3 bounces" in mail.json()["detail"]                        # nothing is sent while held
    assert inbound._rates(store, now())["bounces"] == 0 and not store.read("settings").get("sending_paused")   # Mas Callnet is not paused by it
    assert c.post(f"/api/console/accounts/{aid}/release-hold", headers=hb).status_code == 403
    assert c.post(f"/api/console/accounts/{aid}/release-hold", headers=hs).status_code == 200
    assert "auto_hold" not in accounts.find(store, aid) and accounts.hold_reason(store, aid, "email") is None
    assert audit_rows(store, "account.release_hold", aid)
    assert c.post(f"/api/console/accounts/{aid}/release-hold", headers=hs).status_code == 400


def test_complaint_rate_holds_too_and_small_or_clean_senders_are_left_alone(tmp_path, monkeypatch):
    c, hs, hb, aid, store = client_with_traffic(tmp_path, monkeypatch, sent=60, complaints=1)       # 1.7% complaints (> 0.3%)
    assert ab.check_deliverability(store) == 1 and accounts.find(store, aid)["auto_hold"]["kind"] == "complaint_rate"
    few = tmp_path / "few"
    few.mkdir()
    c2, _, _, aid2, store2 = client_with_traffic(few, monkeypatch, sent=ab.MIN_SENDS_FOR_RATE - 1, bounces=5)     # too few sends to judge
    assert ab.check_deliverability(store2) == 0 and "auto_hold" not in accounts.find(store2, aid2)
    clean = tmp_path / "clean"
    clean.mkdir()
    c3, _, _, aid3, store3 = client_with_traffic(clean, monkeypatch, sent=100, bounces=2)           # exactly 2%: not above
    assert ab.check_deliverability(store3) == 0


def test_the_scan_runs_the_checks_but_only_every_few_minutes(tmp_path, monkeypatch):
    c, hs, hb, aid, store = client_with_traffic(tmp_path, monkeypatch, sent=60, bounces=3)
    assert ab.scan(store, force=True)["held"] == 1
    assert ab.scan(store) == {}                                         # the minute timer calls it every minute; it works every 10


# ---------------------------------------------------------------- failed sign-ins on a client login
def test_failed_signins_on_a_client_login_alert_the_super_admins_once(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    store = rig["store"]
    for _ in range(4):
        login_log.record(store, None, "login", "admin@bravo.com", "failure", "wrong password")
    assert "failed_logins" not in kinds(store)
    login_log.record(store, None, "login", "admin@bravo.com", "failure", "wrong password")
    assert kinds(store) == ["failed_logins"] and boss_mails(store, "5 failed sign-ins: admin@bravo.com")
    login_log.record(store, None, "login", "admin@bravo.com", "failure", "wrong password")          # the same burst: no second alert
    assert kinds(store) == ["failed_logins"]
    rig["c"].post("/api/users", json={"email": "own@mcn.com", "role": "caller", "password": "long-password-1", "workspaces": []}, headers=rig["ho"])
    for _ in range(6):
        login_log.record(store, None, "login", "own@mcn.com", "failure", "wrong password")          # Mas Callnet's logins keep their own alert (owners)
    assert kinds(store) == ["failed_logins"]


# ---------------------------------------------------------------- a sudden volume spike
def seed_sends(store, aid, today_n, per_day):
    today = now().astimezone(IST).date()
    rows = [{"lead_id": "x", "status": "sent", "account_id": aid, "sent_at_ist_date": today.isoformat()} for _ in range(today_n)]
    for back in range(1, 15):
        rows += [{"lead_id": "x", "status": "sent", "account_id": aid, "sent_at_ist_date": (today - timedelta(days=back)).isoformat()} for _ in range(per_day)]
    store.update("outbox", lambda old: [*old, *rows])


def test_more_than_5x_the_daily_average_alerts_once(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    store, b = rig["store"], rig["b"]
    seed_sends(store, b, today_n=120, per_day=4)                         # average 4 a day, 120 today
    assert ab.check_volume(store) == 1 and ab.check_volume(store) == 0
    assert kinds(store) == ["volume_spike"] and boss_mails(store, "sending spike") and audit_rows(store, "abuse.volume_spike", b)


def test_a_busy_account_a_quiet_start_and_mas_callnet_do_not_alert(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    store, b = rig["store"], rig["b"]
    seed_sends(store, b, today_n=60, per_day=20)                         # busy every day: 60 is 3x its average
    seed_sends(store, accounts.MCN, today_n=900, per_day=1)               # Mas Callnet is not watched
    assert ab.check_volume(store) == 0
    other = tmp_path / "o"
    other.mkdir()
    rig2 = make_rig(other, monkeypatch)
    seed_sends(rig2["store"], rig2["b"], today_n=ab.SPIKE_MIN - 1, per_day=0)       # a spike, but too small to matter
    assert ab.check_volume(rig2["store"]) == 0
