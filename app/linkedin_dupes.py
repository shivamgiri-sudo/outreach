"""One LinkedIn person is contacted once per LinkedIn ID (sender profile) inside one workspace.

Two leads are "the same person" when their profile links reduce to the same key (see profile_key). A different
LinkedIn ID may still contact the person: only the SAME sender profile never contacts them twice. The sender of a lead is
the LinkedIn account that holds it, else the one picked for it / the owner's default; a lead with no sender yet is judged
when an account claims it. The scope is the client account (app/accounts.py). A lead that was itself contacted is never
called a duplicate (it is the original; its follow-ups go on).
"""
from typing import Optional

from . import accounts, workspaces
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
        self.settings = store.read("settings")
        self.by_key = {}
        for l in (leads if leads is not None else store.read("leads")):
            if contacted(l) or held(l) or pending(l):
                key = profile_key(l.get("linkedin_url"))
                if key:
                    self.by_key.setdefault((workspaces.ws_of(l), key), []).append(l)

    def sender(self, lead: dict, account: str = "") -> str:
        """The LinkedIn ID that sends for this lead: the asking account, the one holding it, else picked / owner default."""
        from . import linkedin_accounts as accts
        return account or ((lead.get("linkedin_seq") or {}).get("account") or "") or accts.assigned(lead, self.settings)

    def of(self, lead: dict, account: str = "") -> Optional[dict]:
        """{lead_id, name, reason, ...} of the lead that already has this person from the same LinkedIn ID, or None."""
        key = profile_key((lead or {}).get("linkedin_url"))
        me = self.sender(lead, account) if key else ""
        if not key or not me or contacted(lead) or held(lead):
            return None
        rows = [o for o in self.by_key.get((workspaces.ws_of(lead), key), []) if o.get("id") != lead.get("id")
                and self.sender(o) == me and (contacted(o) or held(o) or not lead.get("linkedin_seq"))]
        if not rows:
            return None
        rows.sort(key=lambda o: (not contacted(o), (o.get("linkedin_seq") or {}).get("started_at") or ""))
        o = rows[0]
        seq = o.get("linkedin_seq") or {}
        return {"lead_id": o["id"], "name": o.get("contact_name"), "company": o.get("company"), "account": seq.get("account") or "",
                "reason": _reason(o)}


def duplicate_of(store, lead: dict, account: str = "") -> Optional[dict]:
    return Index(store).of(lead, account)


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


def _status(l: dict) -> str:
    seq = l.get("linkedin_seq") or {}
    if seq.get("status") == "stopped":
        return "stopped" + (f" ({seq['stopped']})" if seq.get("stopped") else "")
    if seq.get("accepted_at"):
        return "connected"
    if contacted(l):
        return "connection request sent, waiting"
    if seq:
        return "on the sequence, nothing sent yet"
    return "not started"


def contacts(store, lead: dict) -> list:
    """Who in THIS lead's workspace is connected to or has contacted this person: one row per lead with the same profile
    (the lead itself included): the user, the LinkedIn ID, where it stands and the dates. Other workspaces never appear."""
    key = profile_key((lead or {}).get("linkedin_url"))
    if not key:
        return []
    ws = workspaces.ws_of(lead)
    names = {w.get("id"): w.get("name") for w in workspaces.all_workspaces(store)}
    out = []
    for l in store.read("leads"):
        if workspaces.ws_of(l) != ws or profile_key(l.get("linkedin_url")) != key:
            continue
        seq = l.get("linkedin_seq") or {}
        if not seq and not l.get("linkedin_dup_of") and l.get("id") != lead.get("id"):
            continue
        out.append({"lead_id": l["id"], "this_lead": l.get("id") == lead.get("id"), "lead": l.get("contact_name") or l.get("company"),
                    "user": seq.get("enrolled_by") or l.get("owner") or "", "workspace": names.get(ws) or ws,
                    "linkedin_account": seq.get("account") or l.get("linkedin_account") or "", "status": _status(l),
                    "sent_on": (seq.get("last_sent_at") or "")[:10], "connected_on": (seq.get("accepted_at") or "")[:10],
                    "steps_done": sorted(seq.get("done") or [])})
    return out
