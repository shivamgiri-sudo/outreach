"""Shared fixtures for the inbound-replies QA tests: fixture loading, an app with leads, our outbox, fake IMAP."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.channels import config
from app.main import create_app
from app.privacy import asks_for_erasure
from app.replies import classify_reply, strip_quoted

FIX = Path(__file__).parent / "fixtures" / "inbound-replies"
LEADS_CSV = (FIX / "leads.csv").read_text(encoding="utf-8-sig")


def corpus(name: str = "replies_corpus.json") -> list:
    data = json.loads((FIX / name).read_text(encoding="utf-8"))
    blocks = data.get("blocks", {})

    def expand(text: str) -> str:
        for _ in range(3):
            text = re.sub(r"\{\{(\w+)\}\}", lambda m: blocks[m.group(1)], text)
        return text
    return [{**r, "text": expand(r["text"])} for r in data["replies"]]


def label_of(text: str) -> str:
    """The label apply_reply settles on before any AI step (classifier + DPDP erasure override)."""
    clean = strip_quoted(text) or (text or "").strip()
    label = classify_reply(clean)
    if asks_for_erasure(clean) and label not in ("unsubscribe", "bounce", "auto_reply"):
        label = "unsubscribe"
    return label


def app_with_leads(tmp_path, **kw):
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kw))
    assert c.post("/api/leads/import", json={"csv": LEADS_CSV, "source": "apollo"}).status_code == 200
    return c, c.app.state.store


def leads_by_email(store) -> dict:
    return {l["email"]: l for l in store.read("leads")}


def seed_outbox(store, sent_at="2026-11-03T04:30:00+00:00") -> dict:
    """One sent cold email per lead, as the sequence runner records it. Returns {email: our Message-ID}."""
    ids = {}
    rows = []
    for i, (email, lead) in enumerate(leads_by_email(store).items()):
        ids[email] = f"<{i:02d}f3a9c1e.{lead['id']}@mail.outreach.example>"
        rows.append({"lead_id": lead["id"], "to": email, "status": "sent", "message_id": ids[email],
                     "provider_id": f"em_{i:02d}", "sent_at": sent_at, "sent_at_ist_date": sent_at[:10]})
    store.update("outbox", lambda r: [*r, *rows])
    return ids


def eml(name: str, msgid: str = "") -> bytes:
    raw = (FIX / "eml" / name).read_bytes()
    return raw.replace(b"{{OUR_MSGID}}", msgid.encode())


def save_imap(store, mark_seen="yes"):
    config.save_provider(store, "inbound_email", "imap", {"imap_host": "imap.example.in", "imap_port": "993",
                                                          "username": "priya@mail.outreach.example", "password": "app-pass",
                                                          "folder": "INBOX", "mark_seen": mark_seen})


def webhook_payload(name: str, **subs):
    text = (FIX / "webhooks.json").read_text(encoding="utf-8")
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return json.loads(text)[name]
