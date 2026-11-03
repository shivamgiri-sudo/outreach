"""QA: reply classifier on a 244-reply corpus (208 tuned + 36 hold-out), and the full reply pipeline at volume.

Safety rules from PRD §4 checked on every reply: a no is never a yes (consent only from a real yes), every
stop/erasure request unsubscribes and suppresses, auto-replies never stop or move a lead, closed leads stay closed.
"""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.inbound import apply_reply
from app.main import create_app
from app.replies import LABELS, classify_reply, strip_quoted
from tests.qa.inbound_replies_support import corpus, label_of

CORPUS = corpus()
HOLDOUT = corpus("replies_holdout.json")
ALL = CORPUS + HOLDOUT


def test_corpus_is_big_and_covers_every_label():
    assert len(CORPUS) >= 150 and len(ALL) >= 240
    assert set(r["expected"] for r in ALL) == set(LABELS) | {"other"}
    assert len({r["id"] for r in ALL}) == len(ALL)


@pytest.mark.parametrize("reply", CORPUS, ids=[r["id"] for r in CORPUS])
def test_corpus_reply_gets_the_expected_label(reply):
    assert label_of(reply["text"]) == reply["expected"]


def test_accuracy_per_label_on_the_hold_out_set():
    total, right = Counter(), Counter()
    for r in HOLDOUT:
        total[r["expected"]] += 1
        right[r["expected"]] += label_of(r["text"]) == r["expected"]
    assert sum(right.values()) / sum(total.values()) >= 0.9
    for label in ("unsubscribe", "bounce", "not_interested"):     # the labels that protect people must not slip
        assert right[label] == total[label], label


@pytest.mark.parametrize("reply", ALL, ids=[r["id"] for r in ALL])
def test_a_no_is_never_read_as_a_yes(reply):
    if reply["expected"] != "interested":
        assert label_of(reply["text"]) != "interested"


@pytest.mark.parametrize("text,label", [
    # quoted footers / headers in other languages must not decide the label (they carry our "Unsubscribe" link)
    ("Haan, bhej dijiye.\n\nप्रेषक: Priya <priya@mail.outreach.example>\n\nNot relevant? Unsubscribe: https://o.example/u/x", "interested"),
    ("Yes, send the sample.\n\nVon: Priya <priya@mail.outreach.example>\nUnsubscribe: https://o.example/u/x", "interested"),
    # an email that is only our quoted email is not an unsubscribe
    ("On Tue, 3 Nov 2026 at 10:02, Priya <p@mail.outreach.example> wrote:\n> Unsubscribe: https://o.example/u/x", "other"),
    # signatures and disclaimers are not read ("Sure Shot Placements", "Yes Staffing", "delete all copies")
    ("Maybe later.\n\nRegards,\nSunil\nDirector, Sure Shot Placements", "not_now"),
    ("Noted.\n\nDISCLAIMER: If received in error please delete all copies of this email.", "other"),
    # sarcasm and soft refusals
    ("Yeah right, as if we need another AI tool.", "not_interested"),
    ("Sure, because what we really need is more vendor emails. No.", "not_interested"),
    ("We're good, thanks.", "not_interested"),
    ("We're good to go, send it.", "interested"),
    # auto-replies that used to read as a yes ("call me", "contact me", "Sure")
    ("I am on vacation until 15th November. For anything urgent, call me on 90000 10005.", "auto_reply"),
    ("Your request (#38211) has been received and is being reviewed by our support staff.", "auto_reply"),
    # bounces in qmail / Exim / Gmail wording
    ("I'm afraid I wasn't able to deliver your message to the following addresses.", "bounce"),
    ("A message that you sent could not be delivered to one or more of its recipients.", "bounce"),
    ("** Delivery incomplete **\nThere was a temporary problem delivering your message.", "auto_reply"),
    # DPDP erasure wording and Hindi stop requests
    ("Under the DPDP Act I request erasure of my personal data held by you.", "unsubscribe"),
    ("Dobara mail mat karna.", "unsubscribe"),
    ("Never contact me again.", "unsubscribe"),
])
def test_regressions_for_fixed_misclassifications(text, label):
    assert label_of(text) == label


def test_classifier_survives_odd_input():
    for text in ("", "   ", "\n\n>\n>", "?", "🙏", "a" * 50_000, "​﻿Yes"):
        assert classify_reply(text) in (*LABELS, "other")
    assert strip_quoted(None) == ""


# ---------- the whole pipeline at volume ----------
FIRST = ["Rakesh", "Pooja", "Imran", "Gurpreet", "Lakshmi", "Arjun", "Fatima", "Venkat", "Neha", "Joseph"]


def _volume_csv(n: int) -> str:
    rows = ["company,contact_name,email,phone,website,open_roles,company_size,country"]
    for i in range(n):
        name = FIRST[i % len(FIRST)]
        rows.append(f"Firm {i:03d} Pvt. Ltd.,{name.upper() if i % 7 == 0 else name} {i:03d},{name.lower()}{i:03d}@firm{i:03d}.example,"
                    f"+91 90000 1{i:04d},https://firm{i:03d}.example,3,120,IN")
    return "\n".join(rows) + "\n"


def test_every_corpus_reply_through_apply_reply(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    assert c.post("/api/leads/import", json={"csv": _volume_csv(len(ALL)), "source": "apollo"}).status_code == 200
    store = c.app.state.store
    leads = sorted(store.read("leads"), key=lambda l: l["email"])
    assert len(leads) == len(ALL)
    for lead in leads:
        assert c.post(f"/api/leads/{lead['id']}/enroll").status_code == 200
    results = {}
    for lead, reply in zip(leads, ALL):
        results[lead["id"]] = (reply, apply_reply(store, lead["id"], reply["text"], "email", source="qa"))
    saved = {l["id"]: l for l in store.read("leads")}
    suppressed = set(store.read("suppressed"))
    tasks = Counter((t["lead_id"], t["kind"]) for t in store.read("tasks"))
    withdrawn = {r["lead_id"] for r in store.read("consent_ledger") if r["event"] == "withdrawn"}
    for lead_id, (reply, result) in results.items():
        lead, label = saved[lead_id], result["label"]
        assert lead["timeline"][-1]["text"].startswith(f"[{label}]")
        assert bool(lead.get("consent")) == (label == "interested"), reply["id"]      # consent only from a yes
        if reply["expected"] != "interested":
            assert not lead.get("consent"), reply["id"]
        if label == "auto_reply":
            assert lead["stage"] == "new" and not lead["sequence"].get("stopped"), reply["id"]
        else:
            assert lead["sequence"]["stopped"], reply["id"]                           # any real reply stops the sequence
        if label == "unsubscribe":
            assert lead["stage"] == "unsubscribed" and lead["email"] in suppressed and lead_id in withdrawn
        if label == "bounce":
            assert lead["stage"] == "disqualified" and lead["email"] in suppressed
        if label in ("question", "wrong_person", "other"):
            assert tasks[(lead_id, label)] == 1, reply["id"]
    erasure = [lid for (lid, kind) in tasks if kind == "erasure_request"]
    assert erasure and all(saved[lid]["stage"] == "unsubscribed" for lid in erasure)


def test_closed_leads_stay_closed_whatever_they_reply(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": _volume_csv(3), "source": "apollo"})
    store = c.app.state.store
    unsub, customer, _ = sorted(store.read("leads"), key=lambda l: l["email"])
    store.update_lead(unsub["id"], lambda l: {**l, "stage": "unsubscribed"})
    store.update_lead(customer["id"], lambda l: {**l, "stage": "customer"})
    for text in ("Yes please send it", "Interested, call me", "No thanks", "Maybe next month"):
        for lead in (unsub, customer):
            out = apply_reply(store, lead["id"], text)
            assert out["effect"] in ("review", "logged")
    saved = {l["id"]: l for l in store.read("leads")}
    assert saved[unsub["id"]]["stage"] == "unsubscribed" and not saved[unsub["id"]].get("consent")
    assert saved[customer["id"]]["stage"] == "customer"
    assert apply_reply(store, unsub["id"], "STOP")["label"] == "unsubscribe"
    assert sum(1 for r in store.read("consent_ledger") if r["lead_id"] == unsub["id"] and r["event"] == "withdrawn") == 0
