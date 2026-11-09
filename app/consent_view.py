"""One lead's consent record: every opt-in / opt-out / do-not-contact event, and where each channel stands now.

Built from what is already saved: the append-only `consent_ledger` (app/privacy.py), the lead's timeline (consent,
unsubscribe and do-not-contact events from before the ledger, or written without one), the lead's own consent record,
the suppressed list (unsubscribes and bounces), the do-not-contact flag and the landline rule (app/contact_rules.py).
"""
from datetime import datetime

from . import privacy
from .contact_rules import LANDLINE_REASON, consent_channels, lead_is_landline

CHANNELS = ("email", "whatsapp", "sms", "call")
LABELS = {"email": "Email", "whatsapp": "WhatsApp", "sms": "SMS", "call": "Call"}
TIMELINE_KINDS = {"consent": "given", "unsubscribe": "withdrawn", "do_not_contact": "do_not_contact"}
SAME_EVENT_SECONDS = 120


def _when(text):
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _close(a, b) -> bool:
    x, y = _when(a), _when(b)
    if not x or not y:
        return False
    if (x.tzinfo is None) != (y.tzinfo is None):
        x, y = x.replace(tzinfo=None), y.replace(tzinfo=None)
    return abs((x - y).total_seconds()) <= SAME_EVENT_SECONDS


def _covers(channel: str, row_channel: str) -> bool:
    ch = str(row_channel or "any").lower()
    return ch == channel or ch in ("any", "")


def events_for(store, lead: dict) -> list:
    lead_id = lead["id"]
    ledger = [r for r in store.read("consent_ledger") if r.get("lead_id") == lead_id]
    out = [{"at": r.get("at"), "event": r.get("event"), "channel": r.get("channel") or "any", "source": r.get("source") or "",
            "source_kind": r.get("source_kind") or privacy.consent_kind(r.get("source"), r.get("by")), "text": r.get("text") or "",
            "by": r.get("by") or "", "from": "consent ledger"} for r in ledger]
    for t in lead.get("timeline") or []:
        event = TIMELINE_KINDS.get(t.get("kind"))
        if not event or any(_close(t.get("at"), r.get("at")) for r in ledger):
            continue
        channel = "email" if t.get("kind") == "unsubscribe" else "any"
        text = str(t.get("text") or "")
        out.append({"at": t.get("at"), "event": event, "channel": channel, "source": text[:200],
                    "source_kind": "person" if t.get("by") else privacy.consent_kind(text, t.get("by")), "text": text[:500],
                    "by": t.get("by") or "", "from": "lead timeline"})
    consent = lead.get("consent") if isinstance(lead.get("consent"), dict) else None
    if consent and not any(e["event"] == "given" for e in out):
        out.append({"at": consent.get("at"), "event": "given", "channel": ",".join(sorted(consent_channels(lead))) or "any",
                    "source": consent.get("source") or "", "source_kind": privacy.consent_kind(consent.get("source")),
                    "text": consent.get("text") or "", "by": consent.get("by") or "", "from": "lead record"})
    if lead.get("do_not_contact") and not any(e["event"] == "do_not_contact" for e in out):
        out.append({"at": lead.get("do_not_contact_at"), "event": "do_not_contact", "channel": "any",
                    "source": "do-not-contact list", "source_kind": "person", "text": "", "by": "", "from": "lead record"})
    out.sort(key=lambda e: str(e.get("at") or ""))
    return out


def status_for(store, lead: dict, events: list) -> dict:
    email = (lead.get("email") or "").lower()
    suppressed = bool(email) and email in {str(e).lower() for e in store.read("suppressed")}
    erased = privacy.is_blocked(store, None, lead.get("phone")) if lead.get("phone") else False
    agreed = consent_channels(lead)
    landline = lead_is_landline(lead)
    out = {}
    for ch in CHANNELS:
        mine = [e for e in events if any(_covers(ch, part) for part in str(e.get("channel") or "any").split(","))]
        last_yes = next((e for e in reversed(mine) if e["event"] == "given"), None)
        last_no = next((e for e in reversed(mine) if e["event"] in ("withdrawn", "do_not_contact")), None)
        later_no = last_no and (not last_yes or str(last_no.get("at") or "") >= str(last_yes.get("at") or ""))
        if lead.get("do_not_contact"):
            row = {"status": "do_not_contact", "why": "on the do-not-contact list"}
        elif lead.get("stage") == "unsubscribed" or erased:
            row = {"status": "opted_out", "why": "unsubscribed" if not erased else "erased on request"}
        elif ch == "email" and suppressed:
            row = {"status": "opted_out", "why": "the address is on the suppressed list (unsubscribe, bounce or complaint)"}
        elif ch in ("whatsapp", "sms") and landline:
            row = {"status": "not_allowed", "why": LANDLINE_REASON}
        elif ch in agreed and not later_no:
            row = {"status": "opted_in", "why": (last_yes or {}).get("source") or "consent recorded"}
        elif later_no:
            row = {"status": "opted_out", "why": last_no.get("source") or "consent withdrawn"}
        else:
            row = {"status": "no_consent", "why": "no yes recorded for this channel"}
        row.update(channel=ch, label=LABELS[ch], since=((last_yes if row["status"] == "opted_in" else last_no) or {}).get("at"))
        out[ch] = row
    return out


def build(store, lead: dict) -> dict:
    events = events_for(store, lead)
    return {"lead_id": lead["id"], "company": lead.get("company") or "", "status": status_for(store, lead, events),
            "events": list(reversed(events))}
