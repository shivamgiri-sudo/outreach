"""One LinkedIn person is contacted once per client account, whichever lead, team member, workspace or LinkedIn account.

Two leads are "the same person" when their profile links reduce to the same key (see profile_key). The scope is the
client account (app/accounts.py), not one workspace. A person counts as already contacted when another lead with the
same key in the account has a connect/message sent, or is currently held by a LinkedIn account. A lead that was itself
contacted is never called a duplicate (it is the original; its follow-ups go on).
"""
from typing import Optional

from . import accounts
from .activities import iso, now_utc


def profile_key(url) -> str:
    """'/in/<slug>' in lower case for any LinkedIn person link (scheme, www/country host, query, fragment, trailing slash and
    percent-encoding do not matter); '' when it is not a /in/<slug> link."""
    from .extension_api import profile_slug
    slug = profile_slug(str(url or ""))
    return f"/in/{slug}" if slug else ""


def contacted(lead: dict) -> bool:
    """A connection request or message went out (same test as app/linkedin_accounts.contacted)."""
    seq = (lead or {}).get("linkedin_seq") or {}
    return bool(1 in (seq.get("done") or []) or seq.get("last_sent_at") or seq.get("accepted_at")
                or seq.get("status") in ("waiting_for_acceptance", "messaging", "finished"))


def held(lead: dict) -> bool:
    """Currently taken by a LinkedIn account (claimed) and the sequence is still running."""
    seq = (lead or {}).get("linkedin_seq") or {}
    return bool(seq.get("account")) and seq.get("status") not in ("stopped", "finished")


def pending(lead: dict) -> bool:
    """On the sequence, nothing sent yet. It blocks only a lead that is not on the sequence at all (so two cannot both start)."""
    seq = (lead or {}).get("linkedin_seq") or {}
    return bool(seq) and seq.get("status") not in ("stopped", "finished")


def _reason(other: dict) -> str:
    seq = other.get("linkedin_seq") or {}
    by = seq.get("enrolled_by") or other.get("owner") or "a teammate"
    when = (seq.get("last_sent_at") or seq.get("accepted_at") or seq.get("started_at") or "")[:10] or "an earlier date"
    acct = seq.get("account") or "LinkedIn"
    who = other.get("contact_name") or other.get("company") or other.get("id")
    if contacted(other):
        return f"Already contacted on LinkedIn by {by} on {when} from {acct} (lead {who})."
    return f"Already being handled on LinkedIn by {by} from {acct} since {when} (lead {who})."


class Index:
    """Contacted/held leads of each client account by profile key, built once for many checks."""

    def __init__(self, store, leads: Optional[list] = None):
        self.amap = accounts.ws_account_map(store)
        self.by_key = {}
        for l in (leads if leads is not None else store.read("leads")):
            if contacted(l) or held(l) or pending(l):
                key = profile_key(l.get("linkedin_url"))
                if key:
                    self.by_key.setdefault((accounts.account_of_lead(self.amap, l), key), []).append(l)

    def of(self, lead: dict) -> Optional[dict]:
        """{lead_id, name, reason, ...} of the lead that already has this person, or None."""
        key = profile_key((lead or {}).get("linkedin_url"))
        if not key or contacted(lead) or held(lead):
            return None
        rows = [o for o in self.by_key.get((accounts.account_of_lead(self.amap, lead), key), []) if o.get("id") != lead.get("id")
                and (contacted(o) or held(o) or not lead.get("linkedin_seq"))]
        if not rows:
            return None
        rows.sort(key=lambda o: (not contacted(o), (o.get("linkedin_seq") or {}).get("started_at") or ""))
        o = rows[0]
        seq = o.get("linkedin_seq") or {}
        return {"lead_id": o["id"], "name": o.get("contact_name"), "company": o.get("company"), "account": seq.get("account") or "",
                "reason": _reason(o)}


def duplicate_of(store, lead: dict) -> Optional[dict]:
    return Index(store).of(lead)


def mark(store, lead_id: str, dup: dict) -> None:
    """Visible marker on the refused lead (once per original): lead['linkedin_dup_of'] and a timeline line."""
    def apply(l):
        if l.get("linkedin_dup_of") == dup["lead_id"] or l.get("linkedin_seq"):
            return None
        return {**l, "linkedin_dup_of": dup["lead_id"], "linkedin_dup_reason": dup["reason"],
                "timeline": [*l["timeline"], {"at": iso(now_utc()), "kind": "linkedin", "text": f"LinkedIn not started: {dup['reason']}"}]}
    store.update_lead(lead_id, apply)


def mirror(store, lead_id: str, text: str) -> None:
    """Show on duplicate leads' timelines what happened to the person on the original lead (accepted, replied)."""
    try:
        def apply(l):
            if l.get("linkedin_dup_of") != lead_id:
                return None
            return {**l, "timeline": [*l["timeline"], {"at": iso(now_utc()), "kind": "linkedin", "text": f"{text} (on the original lead of this person)"}]}
        for l in store.read("leads"):
            if l.get("linkedin_dup_of") == lead_id:
                store.update_lead(l["id"], apply)
    except Exception:
        pass
