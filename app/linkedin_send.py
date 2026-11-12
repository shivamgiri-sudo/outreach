"""What the LinkedIn agent really did, per send: the counted record, the observed-target check and the wrong-send stop.

The ONE row per real send is written by whichever call arrives first (POST /leads/{id}/sent or POST /agent/event) and
filled in by the other (app/linkedin_log.merge_into_recent), so a lost event no longer loses a send from the caps and a
retried event never counts twice. Observed fields (observed_name, observed_url, chat_title, page_title, text_sent) are
optional: an agent that sends none is simply "unverified". Positive evidence of another person (the page, the url or the
chat is not the lead's) pauses THAT LinkedIn account, stops the lead's sequence and raises a red alert.
"""
from datetime import datetime, timedelta
from typing import Optional

from . import linkedin_log as log_mod, linkedin_scope as scope
from .accounts import MCN
from .activities import iso, now_utc
from .reminders import notify

PROOF_FIELDS = ("observed_name", "observed_url", "chat_title", "page_title", "text_sent")
ROW_FIELDS = PROOF_FIELDS + ("step", "to_name", "target_url", "mismatch", "mismatch_why", "unverified", "text_matches")
MERGE_WINDOW = timedelta(minutes=30)


def clean_proof(source) -> dict:
    """The optional observed fields from a request body / event dict, trimmed ('' = absent)."""
    get = source.get if isinstance(source, dict) else lambda k, d="": getattr(source, k, d)
    out = {}
    for k in PROOF_FIELDS:
        v = get(k, "") or ""
        out[k] = str(v).strip()[:2000 if k == "text_sent" else 500]
    return {k: v for k, v in out.items() if v}


def lead_by_id(store, lead_id: str) -> Optional[dict]:
    return next((l for l in store.read("leads") if l["id"] == lead_id), None) if lead_id else None


def send_row(lead: Optional[dict], kind: str, account: str, now: datetime, proof: dict, verdict: dict, extra: dict) -> dict:
    row = {"at": iso(now), "kind": kind, "lead_id": ((lead or {}).get("id") or extra.get("lead_id") or "")[:40], "account": account,
           **{k: v for k, v in extra.items() if k not in ("lead_id", "account") and v not in ("", None)}}
    if lead:
        row.update(to_name=(lead.get("contact_name") or "")[:120], target_url=(lead.get("linkedin_url") or "")[:300])
    row.update({k: v for k, v in {**proof, **verdict}.items() if k in ROW_FIELDS and (v not in ("", None, False) or k == "text_matches" and v is False)})
    return {k: v for k, v in row.items() if v not in ("", None) or k == "lead_id"}


def upsert(store, row: dict, now: datetime, stamp: dict, seen_fn=None) -> tuple:
    """Write `row` into the log (merging with a same-send row written meanwhile). (merged_row, created, previous_row)."""
    out = {}

    def apply(s):
        rows, merged, created, prev = log_mod.merge_into_recent(s.get("linkedin_agent_log") or [], {**row, **stamp}, iso(now - MERGE_WINDOW))
        out.update(row=merged, created=created, prev=prev)
        return {**s, "linkedin_agent_log": rows, **(seen_fn(s) if seen_fn else {})}
    store.update("settings", apply)
    return out["row"], out["created"], out["prev"]


def tell_owner(store, owner: str, title: str, text: str, key: str, every: timedelta, lead_id: str = "", now: Optional[datetime] = None,
               sender=None) -> bool:
    """A bell entry to whoever runs this client account, at most once per `every` for `key`. True when sent."""
    from . import linkedin_watch as watch
    now = now or now_utc()
    state = (store.read("settings").get(watch.STATE_KEY) or {}).get(owner) or {}
    last = (state.get("alerts") or {}).get(key)
    if last and now - datetime.fromisoformat(last) < every:
        return False
    store.update("settings", lambda s: {**s, watch.STATE_KEY: {**(s.get(watch.STATE_KEY) or {}), owner: {
        **state, "alerts": {**(state.get("alerts") or {}), key: iso(now)}}}})
    for who in watch._recipients(store, owner):
        notify(store, who, title, text, lead_id=lead_id or None, kind="linkedin", account_id=owner if owner != MCN else "", sender=sender)
    return True


def wrong_send(store, lead: dict, row: dict, account: str, owner: str, now: datetime, sender=None) -> None:
    """Positive evidence the agent sent to / typed into another person. Pause THIS LinkedIn account until a person resumes
    it (no account known: switch the agent off), stop the lead's sequence (the send is NOT counted as a step), and tell the
    owner, the account's admins and the super admins."""
    from . import linkedin_agent as agent, linkedin_seq as li
    why = (f"WRONG SEND? the step was for {row.get('to_name') or lead.get('contact_name') or '?'} but {row.get('mismatch_why')}. "
           f"Text: {(row.get('text_sent') or '')[:80]}")
    if account:
        agent.pause(store, account, why[:300], now, wrong_send=True, lead_id=lead["id"])
    else:
        agent.save_config(store, {"enabled": False}, owner)
    li.stop(store, lead["id"], "wrong send flagged")
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_wrong_send": {"at": iso(now), "why": why[:300], "account": account},
                                             "timeline": [*l["timeline"], {"at": iso(now), "kind": "linkedin", "alert": True, "text": why[:300]}]})
    title = f"STOP: LinkedIn may have sent to the wrong person ({account or 'agent'} paused)"
    text = (f"{why}. {'Account ' + account if account else 'The agent'} is paused: nothing more will be sent from it. Check the chat on "
            "LinkedIn, apologise or delete the message if needed, then press Resume for that account.")
    if owner == MCN:
        notify(store, store.read("settings").get("linkedin_owner") or "Sales team", title, text, lead_id=lead["id"], kind="linkedin", sender=sender)
    scope.alert_paused(store, owner, account, why[:300])


def record_send_row(store, lead: Optional[dict], kind: str, account: str, owner: str, proof: dict, extra: dict, now: datetime,
                    stamp: dict, seen_fn=None, expected_text: str = "", sender=None) -> tuple:
    """The counted row of one real send (connect / message), the observed-target verdict, and the wrong-send stop on a
    mismatch (once per send). Returns (row, verdict)."""
    from .linkedin_target import check_target
    verdict = check_target(lead, proof, kind, expected_text) if lead else {}
    row = send_row(lead, kind, account, now, proof, verdict, extra)
    merged, _created, prev = upsert(store, row, now, stamp, seen_fn)
    if verdict.get("mismatch") and not (prev or {}).get("mismatch"):
        wrong_send(store, lead, merged, account, owner, now, sender)
    return merged, verdict


def blind_streak(store, account: str, owner: str, now: datetime, sender=None) -> bool:
    """An agent that reported what it saw in the last 7 days but the last 3 sends carry nothing: its selectors broke and
    nobody can see what it does. Pause the account. (An agent that never reports observations is the old agent: left alone.)"""
    from . import linkedin_agent as agent
    rows = [r for r in agent.log_rows(store) if r.get("kind") in ("connect", "message") and r.get("account", "") == account
            and (r.get("account_id") or MCN) == (owner or MCN)]
    since = iso(now - timedelta(days=7))
    saw = any(r.get("observed_name") or r.get("observed_url") for r in rows if r["at"] >= since)
    last3 = rows[-3:]
    if account and saw and len(last3) == 3 and not any(r.get("observed_name") or r.get("observed_url") for r in last3):
        agent.pause(store, account, "3 sends in a row without a page check: the agent can no longer see which person it is messaging", now)
        scope.alert_paused(store, owner, account, "3 sends in a row without a page check: the agent can no longer see which person it is messaging")
        return True
    return False
