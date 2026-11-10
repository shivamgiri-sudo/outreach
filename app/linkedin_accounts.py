"""Which LinkedIn account contacts a lead.

Assignment, strongest first:
  1. the account that has taken the person (linkedin_seq.account: it claimed or contacted them). A person stays with
     that account once step 1 was sent, so follow-ups always come from the account they are connected to;
  2. the account picked for the lead by a person (lead["linkedin_account"], per lead or in bulk);
  3. the default account of the lead's owner (settings["linkedin_owner_accounts"], {owner name: account});
  4. nobody: any account may take the lead (the first to claim it keeps it).

The agent queue for an account shows only leads assigned to it plus unassigned ones, a claim of a lead assigned to
another account is refused, and the daily auto-start of an account starts only such leads within that account's caps.
Every change of assignment is written to the lead's timeline and to the audit trail (app/audit.record).
"""
from datetime import datetime
from typing import Optional

from . import audit, lead_access, linkedin_agent as agent, linkedin_seq as li, workspaces
from .activities import iso, now_utc

FIELD = "linkedin_account"
OWNER_MAP = "linkedin_owner_accounts"
MAX_LEADS = 2000
NOT_STARTABLE = ("unsubscribed", "disqualified", "customer", "advocate")


class AssignRefused(ValueError):
    """The change is not allowed; the message says why in plain words."""


def owner_defaults(settings: Optional[dict]) -> dict:
    raw = (settings or {}).get(OWNER_MAP) or {}
    return {str(k): str(v) for k, v in raw.items() if k and v} if isinstance(raw, dict) else {}


def _owner_default(lead: dict, settings: Optional[dict]) -> str:
    owner = str(lead.get("owner") or "").strip().lower()
    if not owner:
        return ""
    return next((acct for name, acct in owner_defaults(settings).items() if name.strip().lower() == owner), "")


def assigned(lead: dict, settings: Optional[dict] = None) -> str:
    """The LinkedIn account this lead belongs to ('' = any account may take it)."""
    lead = lead or {}
    return ((lead.get("linkedin_seq") or {}).get("account") or lead.get(FIELD) or _owner_default(lead, settings) or "")


def how_assigned(lead: dict, settings: Optional[dict] = None) -> str:
    """'taken' (the account claimed / contacted them), 'lead' (picked for this lead), 'owner' (owner default) or ''."""
    if (lead.get("linkedin_seq") or {}).get("account"):
        return "taken"
    if lead.get(FIELD):
        return "lead"
    return "owner" if _owner_default(lead, settings) else ""


def contacted(lead: dict) -> bool:
    """Step 1 (the connection request) was sent: from now on the person never moves to another account."""
    seq = lead.get("linkedin_seq") or {}
    return bool(1 in (seq.get("done") or []) or seq.get("accepted_at") or seq.get("last_sent_at")
                or seq.get("status") in ("waiting_for_acceptance", "messaging", "finished"))


def may_take(lead: dict, account: str, settings: Optional[dict] = None) -> bool:
    return assigned(lead, settings) in ("", account)


def known(store, owner: Optional[str] = None) -> list:
    """LinkedIn accounts the portal has seen (an agent reported them): [{account, name, account_id}]; with `owner` only
    those working for that client account (app/linkedin_scope.py)."""
    from .linkedin_scope import account_of_row
    seen = store.read("settings").get(agent.SETTINGS_ACCOUNTS) or {}
    rows = [{"account": a, "name": (v or {}).get("name", ""), "account_id": account_of_row(v)} for a, v in sorted(seen.items()) if a]
    return [r for r in rows if owner is None or r["account_id"] == owner]


def _clean_account(store, raw, owner: Optional[str] = None) -> str:
    account = agent.norm_account(raw)
    if raw and not account:
        raise AssignRefused("That is not a LinkedIn account.")
    if account and account not in {k["account"] for k in known(store, owner)}:
        raise AssignRefused(f"No LinkedIn account {account}. Accounts appear here once their agent has run.")
    return account


def _actor_name(user) -> str:
    return audit.actor_of(user)["name"] if not isinstance(user, str) else (user or "Unknown")


def set_account(store, lead_ids: list, raw_account: str, user) -> dict:
    """Pick the LinkedIn account for leads ('' = no pick: owner default, else any account). Answers per lead like the
    bulk actions: {asked, ok, failed, results: [{id, ok, reason}], reasons}."""
    from . import accounts as client_accounts
    from .linkedin_scope import slug_account
    account = _clean_account(store, raw_account, client_accounts.current() if client_accounts.current_is_client() else None)
    ids = list(dict.fromkeys(str(i) for i in lead_ids or [] if i))
    if not ids:
        raise AssignRefused("Pick at least one lead.")
    if len(ids) > MAX_LEADS:
        raise AssignRefused(f"At most {MAX_LEADS} leads at a time.")
    settings = store.read("settings")
    amap = client_accounts.ws_account_map(store)
    visible = {l["id"]: l for l in store.read("leads") if workspaces.visible(l)}
    reasons = {i: "not found" for i in ids if i not in visible}
    slug_owner = slug_account(settings, account) if account else None
    for i in ids:                                  # a LinkedIn account only ever takes people of its own client account
        if i in visible and slug_owner is not None and client_accounts.account_of_lead(amap, visible[i]) != slug_owner:
            reasons[i] = "that LinkedIn account works for another client account"
    who, stamp = _actor_name(user), iso(now_utc())
    for lead_id in (i for i in ids if i in visible and i not in reasons):
        seen = {}

        def apply(lead: dict):
            old = assigned(lead, settings)
            if contacted(lead):
                if account == old:
                    seen["why"] = "no change"
                else:
                    seen["why"] = (f"already contacted from LinkedIn account {old}; a person stays with that account" if old
                                   else "the connection request was already sent; a person is never moved mid-sequence")
                return None
            seq = lead.get("linkedin_seq")
            new_lead = {k: v for k, v in lead.items() if k != FIELD}
            if account:
                new_lead[FIELD] = account
            if seq and seq.get("account"):                     # claimed but not contacted yet: free to move
                new_lead["linkedin_seq"] = {k: v for k, v in seq.items() if k != "account"}
            new = assigned(new_lead, settings)
            if new == old and (lead.get(FIELD) or "") == account:
                seen["why"] = "no change"
                return None
            seen.update(old=old, new=new)
            text = f"LinkedIn account: {old or 'any account'} → {new or 'any account'} (by {who})"
            return {**new_lead, "timeline": [*(lead.get("timeline") or []), {"at": stamp, "kind": "linkedin", "text": text, "by": who}]}
        saved = store.update_lead(lead_id, apply)
        if saved is None:
            reasons[lead_id] = "not found"
        elif seen.get("why"):
            reasons[lead_id] = seen["why"]
        else:
            audit.record(store, user, "linkedin.account", saved.get("company") or saved.get("contact_name") or lead_id,
                         f"{seen['old'] or 'any account'} -> {seen['new'] or 'any account'} (lead {lead_id})")
    results = [{"id": i, "ok": i not in reasons, **({"reason": reasons[i]} if i in reasons else {})} for i in ids]
    counts = {}
    for r in results:
        if not r["ok"]:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    ok = sum(1 for r in results if r["ok"])
    return {"account": account, "asked": len(ids), "ok": ok, "failed": len(ids) - ok, "results": results,
            "reasons": [{"reason": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]}


def save_owner_defaults(store, mapping: dict, user) -> dict:
    """Replace the owner -> LinkedIn account defaults. Owners must be known names, accounts known accounts."""
    from .lead_ops import owner_choices
    names = {n.lower(): n for n in owner_choices(store)}
    clean = {}
    for raw_owner, raw_acct in (mapping or {}).items():
        name = names.get(" ".join(str(raw_owner or "").split()).lower())
        if not name:
            raise AssignRefused(f"{raw_owner} is not in the team list.")
        acct = _clean_account(store, raw_acct, "mcn")          # owner defaults: Mas Callnet's team and LinkedIn accounts
        if acct:
            clean[name] = acct
    before = owner_defaults(store.read("settings"))
    store.update("settings", lambda s: {**s, OWNER_MAP: clean})
    for name in sorted(set(before) | set(clean)):
        if before.get(name, "") != clean.get(name, ""):
            audit.record(store, user, "linkedin.owner_account", name,
                         f"default LinkedIn account {before.get(name) or 'none'} -> {clean.get(name) or 'none'}")
    return clean


def auto_start_for(store, account: str, max_new: int, now: Optional[datetime] = None, owner: str = "mcn", user: Optional[dict] = None) -> list:
    """Daily auto-start for one polling account: start leads assigned to it (first) or unassigned, never more than
    the daily limit for this account (counting its people started today) or `max_new` (what it may still send today).
    A lead started for an account is taken by it, so the next account to poll starts other people. With lead ownership on
    (app/lead_access.py) only leads `user` may send to are started (no user: leads that have an owner)."""
    now = now or now_utc()
    settings = store.read("settings")
    cfg = agent.config(store, agent.owner_of_slug(store, account, owner))       # the client account's own switch and limit
    if not cfg["auto_start"] or max_new <= 0:
        return []
    today = iso(now)[:10]
    leads = workspaces.filter_rows(store.read("leads"))                         # the agent token's own client account
    limit = int(cfg["daily_connections"])
    started_today = sum(1 for l in leads if (l.get("linkedin_seq") or {}).get("started_at", "")[:10] == today
                        and (not account or assigned(l, settings) == account))
    room = min(max_new, limit - started_today)
    if room <= 0:
        return []
    from . import linkedin_dupes
    dupes = linkedin_dupes.Index(store)                                         # a person already contacted through another lead is skipped
    pool = [l for l in leads if not l.get("linkedin_seq") and not dupes.of(l) and li.has_profile_link(l) and l.get("stage") not in NOT_STARTABLE
            and not l.get("do_not_contact") and may_take(l, account, settings) and workspaces.visible(l) and lead_access.may_start(store, user, l)]
    pool.sort(key=lambda l: (assigned(l, settings) != account if account else False, -(l.get("open_roles") or 0)))
    started = []
    for lead in pool:
        if len(started) >= room:
            break
        try:
            li.start(store, lead["id"], now, auto=True)
        except (LookupError, ValueError):
            continue
        if account:
            store.update_lead(lead["id"], lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": l["linkedin_seq"].get("account") or account},
                                                     "timeline": [*l["timeline"], {"at": iso(now), "kind": "linkedin", "text": f"LinkedIn account {account} took this person"}]})
        started.append({"lead_id": lead["id"], "company": lead.get("company"), "name": lead.get("contact_name"), "title": lead.get("title")})
    return started
