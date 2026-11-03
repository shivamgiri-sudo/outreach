"""QA: replies and bounces arriving for real — raw RFC 822 mail over (fake) IMAP and provider webhooks over HTTP.

Every external system is a fake: FakeIMAP from tests/channel_fakes.py, a recording email transport, and webhook
payloads shaped after the providers' public docs (fixtures/inbound-replies/webhooks.json; sources in the report).
"""
import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

from app.channels.imap_poll import poll_imap
from app.channels.inbound_parse import delivery_events, email_from_mime
from app.inbound import process_delivery_event, process_inbound_message
from tests.channel_fakes import FakeIMAP
from tests.qa.inbound_replies_support import app_with_leads, eml, leads_by_email, save_imap, seed_outbox, webhook_payload

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)          # Tuesday 10:30 IST
RAKESH, ZOE, KAVITA = "rakesh.verma@quickserve.example", "zoe.dsouza@cafebahar.example", "kavita.hr@gmail.example"
SUNRISE, BALAJI, ANKIT = "talent@sunrise-pharma.example", "hr@balaji-auto.example", "ankit_rao1990@rediffmail.example"
MEENA = "meena.pillai@kaveri-textiles.example"


def mailbox(ids: dict) -> dict:
    """The reply mailbox on Wednesday morning: replies, bounces, auto-replies, a newsletter, a stranger."""
    return {201: eml("01_gmail_qp_hinglish.eml", ids[RAKESH]), 202: eml("02_outlook_base64_latin1.eml", ids[ZOE]),
            203: eml("03_gmail_dsn.eml", ids[KAVITA]), 204: eml("04_exchange_ndr_no_thread_headers.eml", ids[SUNRISE]),
            205: eml("05_zoho_dsn_rfc822_headers.eml", ids[BALAJI]), 206: eml("06_rediff_qmail_inline.eml", ids[ANKIT]),
            207: eml("07_ooo_autoreply.eml", ids[MEENA]), 208: eml("08_zendesk_ack.eml", ids[RAKESH]),
            209: eml("09_newsletter.eml"), 210: eml("10_unmatched_reply.eml"), 211: eml("11_other_contact_erasure_hindi.eml")}


# ---------- MIME decoding ----------
def test_mime_decoding_quoted_printable_base64_latin1_and_threading():
    qp = email_from_mime(eml("01_gmail_qp_hinglish.eml", "<our-1@mail.outreach.example>"), "imap", "201")
    assert qp["text"].startswith("Haan ji, sample bhej dijiye. Kal 11 baje ke baad call kar lena — 90000 10011.")
    assert qp["from_email"] == RAKESH and qp["in_reply_to"] == "<our-1@mail.outreach.example>" and "<div" in qp["html"]
    assert qp["received_at"] == "2026-11-04T06:12:07+00:00"
    latin = email_from_mime(eml("02_outlook_base64_latin1.eml", "<our-2@mail.outreach.example>"), "imap", "202")
    assert "Zoë D'Souza" in latin["text"] and "Café Bahar" in latin["text"]
    assert latin["in_reply_to"] == "" and latin["references"][-1] == "<our-2@mail.outreach.example>"
    hindi = email_from_mime(eml("11_other_contact_erasure_hindi.eml"), "imap")
    assert hindi["from_email"] == "sunil.rao@quickserve.example" and hindi["text"].startswith("Mera data delete")


def test_bounce_reports_without_thread_headers_point_at_our_message():
    """Regression: Exchange/Postfix/Zoho/qmail bounce reports carry our Message-ID only inside the report."""
    for name in ("04_exchange_ndr_no_thread_headers.eml", "05_zoho_dsn_rfc822_headers.eml", "06_rediff_qmail_inline.eml"):
        assert email_from_mime(eml(name, "<ours@mail.outreach.example>"), "imap")["in_reply_to"] == "<ours@mail.outreach.example>", name
    assert email_from_mime(eml("09_newsletter.eml"), "imap")["in_reply_to"] == ""
    fwd = (b"From: a@b.example\nSubject: Fwd: hi\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=x\n\n--x\n"
           b"Content-Type: text/plain\n\nsee below\n--x\nContent-Type: message/rfc822\n\nMessage-ID: <ours@m.example>\n\nhi\n--x--\n")
    assert email_from_mime(fwd, "imap")["in_reply_to"] == ""        # a forwarded attachment is not a bounce report


# ---------- IMAP polling end to end ----------
def test_imap_poll_handles_a_realistic_morning_mailbox(tmp_path):
    fake = {}
    c, store = app_with_leads(tmp_path, imap_factory=lambda host, port: fake["imap"])
    ids = seed_outbox(store)
    leads = leads_by_email(store)
    assert RAKESH in leads and len(leads) == 7                   # BOM, CAPS, spaces, blank row all handled on import
    store.update_lead(leads[RAKESH]["id"], lambda l: {**l, "other_contacts": [{"name": "Sunil Rao", "email": "Sunil.Rao@Quickserve.example"}]})
    save_imap(store)
    fake["imap"] = FakeIMAP(mailbox(ids))
    out = c.post("/api/channels/inbound_email/imap/poll").json()
    assert out["ok"] and out["fetched"] == 11 and out["processed"] == 11
    leads = leads_by_email(store)
    inbox = {row["message_id"]: row for row in store.read("inbox")}
    assert len(inbox) == 10                                       # the newsletter is not stored
    assert leads[RAKESH]["stage"] == "unsubscribed"               # said yes, then a colleague asked for erasure
    assert any(r["event"] == "given" for r in store.read("consent_ledger") if r["lead_id"] == leads[RAKESH]["id"])
    assert leads[ZOE]["stage"] == "disqualified" and inbox["<PN3PR01MB1234ABCD@PN3PR01MB1234.INDPRD01.PROD.OUTLOOK.example>"]["matched_by"] == "thread"
    for email in (KAVITA, SUNRISE, BALAJI, ANKIT):
        assert leads[email]["stage"] == "disqualified", email
        assert email in store.read("suppressed"), email
    assert leads[MEENA]["stage"] == "new"                         # an out-of-office changes nothing
    assert inbox["<ooo-778@kaveri-textiles.example>"]["label"] == "auto_reply"
    assert inbox["<ZD7781_ticket_4512@quickserve.zendesk.example>"]["label"] == "auto_reply"
    assert inbox["<sunil-erase-1@quickserve.example>"]["matched_by"] == "email"
    kinds = [t["kind"] for t in store.read("tasks")]
    assert kinds.count("unmatched_reply") == 1 and kinds.count("erasure_request") == 1
    assert "\\Seen" in str(fake["imap"].stored[0])
    assert c.post("/api/channels/inbound_email/imap/poll").json()["processed"] == 0       # nothing is read twice


def test_the_same_message_redelivered_under_a_new_uid_is_handled_once(tmp_path):
    fake = {}
    c, store = app_with_leads(tmp_path, imap_factory=lambda host, port: fake["imap"])
    ids = seed_outbox(store)
    save_imap(store, mark_seen="no")
    reply = eml("02_outlook_base64_latin1.eml", ids[ZOE])
    fake["imap"] = FakeIMAP({7: reply})
    assert c.post("/api/channels/inbound_email/imap/poll").json()["processed"] == 1
    fake["imap"] = FakeIMAP({7: reply, 8: reply})                 # moved between folders / server re-indexed
    assert c.post("/api/channels/inbound_email/imap/poll").json()["processed"] == 1
    assert len(store.read("inbox")) == 1 and leads_by_email(store)[ZOE]["engagement"]["replies"] == 1
    assert fake["imap"].stored == []                              # read-only mode never marks mail as read


def test_imap_processor_crash_retries_the_message_next_time(tmp_path):
    _, store = app_with_leads(tmp_path)
    ids = seed_outbox(store)
    save_imap(store)
    fake = FakeIMAP({1: eml("01_gmail_qp_hinglish.eml", ids[RAKESH]), 2: eml("07_ooo_autoreply.eml", ids[MEENA])})
    calls = []

    def flaky(s, msg):
        calls.append(msg["raw_id"])
        if len(calls) == 2:
            raise OSError("disk full")
        return process_inbound_message(s, msg)
    first = poll_imap(store, flaky, lambda h, p: fake, now=T0)
    assert first["ok"] is False and first["processed"] == 1 and "Message 2" in first["message"]
    again = poll_imap(store, process_inbound_message, lambda h, p: fake, now=T0 + timedelta(minutes=5))
    assert again["processed"] == 1 and len(store.read("inbox")) == 2


def test_reply_stops_the_sequence_over_days_but_an_auto_reply_does_not(tmp_path):
    sent = []
    fake = {}

    def transport(url, headers, body):
        sent.append(body)
        return {"id": f"em_{len(sent)}"}
    c, store = app_with_leads(tmp_path, email_transport=transport, imap_factory=lambda h, p: fake["imap"])
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.outreach.example", "from_name": "Priya", "from_email": "hello@mail.outreach.example", "api_key": "re_test"}})
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in", "sender_company": "BookMyInterview.ai",
                                 "sender_address": "Sector 62, Noida 201309, India"})
    assert c.put("/api/settings", json={"live_sending": True}).status_code == 200
    for lead in store.read("leads"):
        c.post(f"/api/leads/{lead['id']}/enroll")
    def run_day(day):                                             # a few runs per day: each run sends only a few
        for minutes in (0, 40, 80, 120):
            c.post("/api/sequences/run", json={"now": (T0 + timedelta(days=day, minutes=minutes)).isoformat()})
    run_day(0)
    outbox = store.read("outbox")
    ids = {m["to"]: m["message_id"] for m in outbox if m.get("message_id")}
    assert {RAKESH, MEENA, ZOE} <= set(ids)
    save_imap(store)
    fake["imap"] = FakeIMAP({1: eml("01_gmail_qp_hinglish.eml", ids[RAKESH]), 2: eml("07_ooo_autoreply.eml", ids[MEENA])})
    assert c.post("/api/channels/inbound_email/imap/poll").json()["processed"] == 2
    for day in (3, 4, 7, 8):                                      # Friday, Saturday, next Tuesday, Wednesday
        run_day(day)
    per_lead = {}
    for m in store.read("outbox"):
        if m.get("status") == "sent":
            per_lead[m["to"]] = per_lead.get(m["to"], 0) + 1
    assert per_lead[RAKESH] == 1                                  # replied: never another cold email
    assert per_lead[MEENA] >= 2 and per_lead[ZOE] >= 2            # the out-of-office and the silent lead carry on
    rakesh = leads_by_email(store)[RAKESH]
    assert rakesh["sequence"]["stopped"] == "replied" and rakesh["stage"] == "interested"


# ---------- provider webhooks over HTTP ----------
def _hook(c, channel, provider, values=None):
    assert c.put(f"/api/channels/{channel}/{provider}", json={"values": values or {}}).status_code == 200
    data = c.get(f"/api/channels/webhooks?channel={channel}&provider={provider}").json()["webhooks"][0]
    return data["url"].split("8000", 1)[1], data["secret"]


def test_postmark_inbound_reply_matches_thread_and_dedupes_retries(tmp_path):
    c, store = app_with_leads(tmp_path)
    ids = seed_outbox(store)
    url, _ = _hook(c, "inbound_email", "postmark_inbound")
    payload = webhook_payload("postmark_inbound", OUR_MSGID=ids[MEENA])
    assert c.post(url.split("?")[0], json=payload).status_code == 401           # no secret, no entry
    first = c.post(url, json=payload).json()
    assert first == {"ok": True, "messages": 1, "matched": 1}
    assert c.post(url, json=payload).json()["matched"] == 0                      # Postmark retry: handled once
    row = store.read("inbox")[0]
    assert row["matched_by"] == "thread" and row["label"] == "not_now" and "Unsubscribe" not in row["text"]
    meena = leads_by_email(store)[MEENA]
    assert meena["stage"] == "not_now" and meena["email"] not in store.read("suppressed")


def test_sendgrid_inbound_parse_multipart_with_latin1_charset(tmp_path):
    c, store = app_with_leads(tmp_path)
    ids = seed_outbox(store)
    url, _ = _hook(c, "inbound_email", "sendgrid_inbound", {"reply_domain": "reply.outreach.example"})
    text = "Interested. Pls call on 90000 10002 after 4 pm.\n\nRegards,\nZoë, Café Bahar".encode("iso-8859-1")
    files = {"headers": (None, f"Message-ID: <sg-in-1@cafebahar.example>\nIn-Reply-To: {ids[ZOE]}\nDate: Wed, 4 Nov 2026 12:00:00 +0530\n"),
             "from": (None, "=?iso-8859-1?q?Zo=EB?= <zoe.dsouza@cafebahar.example>"), "to": (None, "replies@reply.outreach.example"),
             "subject": (None, "Re: Hiring at Cafe Bahar"), "text": (None, text), "html": (None, "<p>Interested</p>"),
             "envelope": (None, '{"to":["replies@reply.outreach.example"],"from":"zoe.dsouza@cafebahar.example"}'),
             "charsets": (None, '{"to":"UTF-8","html":"UTF-8","subject":"UTF-8","from":"UTF-8","text":"iso-8859-1"}'),
             "SPF": (None, "pass"), "sender_ip": (None, "203.0.113.9")}
    assert c.post(url, files=files).json() == {"ok": True, "messages": 1, "matched": 1}
    row = store.read("inbox")[0]
    assert row["label"] == "interested" and row["matched_by"] == "thread" and row["received_at"] == "2026-11-04T06:30:00+00:00"
    assert leads_by_email(store)[ZOE]["consent"]["source"] == "email reply"


def test_resend_inbound_has_no_body_so_it_becomes_a_task_not_a_decision(tmp_path):
    """Product limitation: Resend's email.received webhook carries metadata only (no text, no headers)."""
    c, store = app_with_leads(tmp_path)
    seed_outbox(store)
    secret = "whsec_" + base64.b64encode(b"resend-qa-signing-key-32-bytes!!").decode()
    url, _ = _hook(c, "inbound_email", "resend_inbound", {"signing_secret": secret})
    path = url.split("?")[0]
    body = __import__("json").dumps(webhook_payload("resend_received")).encode()
    stamp, msg_id = str(int(time.time())), "msg_2Kq9Lw1"
    sig = base64.b64encode(hmac.new(base64.b64decode(secret[6:]), f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    headers = {"Content-Type": "application/json", "svix-id": msg_id, "svix-timestamp": stamp}
    assert c.post(path, content=body, headers={**headers, "svix-signature": "v1,AAAA"}).status_code == 401
    assert c.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401   # token not enough once signed
    old = str(int(time.time()) - 3600)
    assert c.post(path, content=body, headers={**headers, "svix-timestamp": old, "svix-signature": f"v1,{sig}"}).status_code == 401
    assert c.post(path, content=body, headers={**headers, "svix-signature": f"v1,{sig}"}).json()["matched"] == 1
    row = store.read("inbox")[0]
    assert row["matched_by"] == "email" and row["label"] == "other"          # only the subject was there to read
    rakesh = leads_by_email(store)[RAKESH]
    assert rakesh["stage"] == "engaged" and not rakesh.get("consent")
    assert [t["kind"] for t in store.read("tasks")] == ["other"]


def test_sendgrid_event_webhook_batch(tmp_path):
    c, store = app_with_leads(tmp_path)
    seed_outbox(store)
    store.update("outbox", lambda rows: [{**m, "provider_id": "dd44"} if m["to"] == ZOE else m for m in rows])
    url, _ = _hook(c, "email_events", "sendgrid_events")
    assert c.post(url, json=webhook_payload("sendgrid_events")).json() == {"ok": True, "events": 3}
    leads = leads_by_email(store)
    assert leads[KAVITA]["stage"] == "disqualified" and KAVITA in store.read("suppressed")
    assert leads[RAKESH]["stage"] == "new" and RAKESH not in store.read("suppressed")      # 'blocked' is our reputation, not the address
    assert leads[MEENA]["stage"] == "new"                                                    # deferred = still trying
    assert leads[BALAJI]["stage"] == "unsubscribed"                                          # spam report withdraws consent
    assert next(m for m in store.read("outbox") if m["to"] == ZOE)["delivered_at"] == "2025-11-03T05:01:35+00:00"
    assert c.post(url, json=webhook_payload("sendgrid_events")).json()["events"] == 3
    assert len(store.read("delivery_events")) == 3                                           # retried batch is not double counted


def test_postmark_bounce_and_spam_webhooks(tmp_path):
    c, store = app_with_leads(tmp_path)
    seed_outbox(store)
    url, _ = _hook(c, "email_events", "postmark_events")
    for name, events in (("postmark_bounce_hard", 1), ("postmark_bounce_soft", 0), ("postmark_spam", 1)):
        assert c.post(url, json=webhook_payload(name, PROVIDER_ID="em_03")).json()["events"] == events, name
    leads = leads_by_email(store)
    assert leads[SUNRISE]["stage"] == "disqualified" and leads[MEENA]["stage"] == "new" and leads[BALAJI]["stage"] == "unsubscribed"
    assert store.read("delivery_events")[0]["at"] == "2026-11-03T05:01:00+00:00"


def test_resend_temporary_bounce_does_not_suppress(tmp_path):
    """Regression: Resend email.bounced with bounce.type Temporary was treated as a hard bounce."""
    assert delivery_events("resend_events", webhook_payload("resend_bounced_temporary", PROVIDER_ID="em_1")) == []
    hard = delivery_events("resend_events", webhook_payload("resend_bounced_permanent", PROVIDER_ID="em_1", OUR_MSGID="<x@y>"))
    assert hard == [{"type": "bounce", "email": SUNRISE, "provider_id": "em_1", "at": "2026-11-03T05:01:00+00:00"}]
    legacy = {"type": "email.bounced", "data": {"email_id": "e", "to": ["a@b.example"]}}       # older payloads without bounce{}
    assert delivery_events("resend_events", legacy)[0]["type"] == "bounce"
    c, store = app_with_leads(tmp_path)
    url, _ = _hook(c, "email_events", "resend_events")
    c.post(url, json=webhook_payload("resend_bounced_temporary", PROVIDER_ID="em_06"))
    c.post(url, json=webhook_payload("resend_complained", PROVIDER_ID="em_04"))
    assert MEENA not in store.read("suppressed") and leads_by_email(store)[MEENA]["stage"] == "new"
    assert leads_by_email(store)[BALAJI]["stage"] == "unsubscribed"


# ---------- bounce / complaint pause thresholds, over days ----------
def _sends(store, n, start, spread_days):
    rows = [{"lead_id": "x", "to": f"p{i}@firm{i}.example", "status": "sent",
             "sent_at": (start + timedelta(hours=i * 24 * spread_days / n)).isoformat(timespec="seconds")} for i in range(n)]
    store.update("outbox", lambda r: [*r, *rows])


def test_bounce_pause_uses_a_rolling_seven_day_window(tmp_path):
    _, store = app_with_leads(tmp_path)
    _sends(store, 200, T0 - timedelta(days=20), 10)               # old sends: outside the window
    _sends(store, 300, T0 - timedelta(days=6), 6)
    for i in range(6):                                            # 6/300 = 2.0%: not above the limit
        assert process_delivery_event(store, {"type": "bounce", "email": f"p{i}@firm{i}.example", "provider_id": f"b{i}"}, now=T0)["paused"] is None
    hit = process_delivery_event(store, {"type": "bounce", "email": "p6@firm6.example", "provider_id": "b6"}, now=T0)
    assert hit["paused"] and hit["rates"]["sent"] == 300 and "7 bounces" in hit["paused"]
    assert store.read("settings")["sending_paused"]["resume_at"] is None


def test_complaint_pause_and_minimum_volume(tmp_path):
    _, store = app_with_leads(tmp_path)
    _sends(store, 49, T0 - timedelta(days=1), 1)
    for i in range(10):                                           # under 50 sends the rate means nothing yet
        assert process_delivery_event(store, {"type": "bounce", "email": f"q{i}@x.example", "provider_id": f"q{i}"}, now=T0)["paused"] is None
    _, store2 = app_with_leads(tmp_path / "b")
    _sends(store2, 334, T0 - timedelta(days=3), 3)
    assert process_delivery_event(store2, {"type": "complaint", "email": "p1@firm1.example", "provider_id": "c1"}, now=T0)["paused"] is None
    assert process_delivery_event(store2, {"type": "complaint", "email": "p2@firm2.example", "provider_id": "c2"}, now=T0)["paused"]
    later = T0 + timedelta(days=8)                                # a week later the window has rolled past them
    assert process_delivery_event(store2, {"type": "complaint", "email": "p3@firm3.example", "provider_id": "c3"}, now=later)["rates"]["sent"] == 0
