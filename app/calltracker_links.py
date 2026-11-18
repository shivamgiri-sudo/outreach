"""DialHalo links: one Outreach workspace can be linked to several DialHalo workspaces (e.g. main -> "MAS Callnet" and "BMI").

Storage: ct_connections {link_id: link}. The collection keeps its name, so app/secretbox.py still seals api_key /
webhook_secret at rest and app/backup.py still scrubs it. A link:
  id, workspace (the Outreach workspace), base_url (server to server), public_url (browser links), api_key, webhook_secret,
  enabled, mapping, via ("connect" = one-click, "manual" = API key form), dh_workspace {id, name, slug}, dh_connection_id,
  dh_webhook_id, lead_sync, primary, members {at, rows: [{name, email, active}]} (GET /api/v1/members, cached),
  last_push / last_event / last_test / last_match, unmapped_outcomes, created_at/by, updated_at/by,
  sync_cursor / last_reconcile / last_reconcile_status (the integration kit's nightly reconcile, app/calltracker_kit.py).

Migration (idempotent; at start-up and on first read): the old shape {workspace: connection} is re-keyed by the
connection's own id, so its receiver URL /api/calltracker/hook/<id> keeps working. It becomes the workspace's primary link
with lead_sync "call_tasks" (today's behaviour) and `legacy: True` (leads pushed before the migration belong to it).
ct_backfill rows keyed by workspace move to the link's id; waiting ct_outbox jobs get the link id. No table is altered:
only store collections are rewritten (JSON file and MySQL alike).

Lead sync (pushes of leads to a DialHalo dial queue), per link:
  off             nothing is pushed to this link
  call_tasks      a lead is pushed when it gets a call task (the default; what one connection did before)
  owners_members  leads whose Outreach owner's email is a member of that DialHalo workspace (members list cached)
  all_with_phone  every lead of the workspace with a phone number
A lead goes to at most ONE link. Among the links that qualify: the link the lead was already pushed to keeps it (no
copies in two DialHalo workspaces when the primary changes), otherwise the primary link, otherwise the oldest.
owners_members and all_with_phone are also swept in the background (app/calltracker_sync.py) for leads never pushed.
Personal links (contract 2026-11-18, personal-connect-contract.md): a DialHalo member who is not an admin may connect
only their OWN calls. Such a link has dh_scope "member" and dh_member {id, name, email} (from GET connect/self; a
missing scope = "workspace"). Several personal links of one DialHalo workspace may sit next to a workspace link.
  * lead sync on a personal link: off | call_tasks | owners_members (all_with_phone is turned into owners_members);
  * a personal link takes a lead only when the lead's owner (or the call task's owner) has the member's e-mail, and
    never when an active workspace link to the same DialHalo workspace exists (that link wins);
  * the same call delivered by a workspace link and a personal link (same DialHalo workspace id) is kept once
    (app/calltracker_events.apply_call);
  * do-not-call goes to the workspace links, and to a personal link only when no workspace link to its DialHalo workspace
    exists (DialHalo limits it to the member's numbers; a row outside answers skipped / not_yours);
  * a 403 {code: personal_connection} skips that job on that link for good (shown on the card); a 401
    {code: connection_paused} shows "Paused in DialHalo" and backs off like any retry.
Calls, outcomes and Outreach-match results always stay with the link the call came from (an outcome changed in Outreach
goes back to that link only); erase, do-not-call and calls logged by hand in Outreach (lead history entries,
app/calltracker_outcomes.py) go to every active link of the workspace.
"""
import uuid
from typing import Optional

LEAD_SYNC = ("off", "call_tasks", "owners_members", "all_with_phone")
SWEEP_MODES = ("owners_members", "all_with_phone")
MEMBERS_TTL_SECONDS = 6 * 3600
PERSONAL_SYNC = ("off", "call_tasks", "owners_members")      # a personal link never takes "every lead with a phone"
EDITABLE = ("base_url", "public_url", "api_key", "webhook_secret", "enabled", "mapping", "lead_sync", "via",
            "dh_workspace", "dh_connection_id", "dh_webhook_id", "members", "dh_scope", "dh_member")


def _ct():
    from . import calltracker
    return calltracker


# ---------------------------------------------------------------- migration
def _needs_migration(rows) -> bool:
    return isinstance(rows, dict) and any(isinstance(v, dict) and v.get("id") and k != v["id"] for k, v in rows.items())


def migrate(store) -> int:
    """Old {workspace: connection} rows become links keyed by their id. Returns how many were moved."""
    moved = {}                                         # workspace -> link id

    def apply(rows: dict) -> dict:
        out = {}
        for key, conn in (rows or {}).items():
            if not isinstance(conn, dict) or not conn.get("id"):
                continue
            if key == conn["id"]:
                out[key] = conn
                continue
            ws = conn.get("workspace") or key
            link = {**conn, "workspace": ws, "lead_sync": conn.get("lead_sync") or "call_tasks", "legacy": True,
                    "via": conn.get("via") or "manual"}
            link["primary"] = not any(l.get("workspace") == ws and l.get("primary") for l in out.values())
            out[conn["id"]] = link
            moved[ws] = conn["id"]
        return out
    store.update("ct_connections", apply)
    if moved:
        store.update("ct_backfill", lambda rows: {moved.get(k, k): v for k, v in (rows or {}).items()})
        store.update("ct_outbox", lambda jobs: [{**j, "link": moved[j["ws"]]} if not j.get("link") and j.get("ws") in moved
                                                and j.get("status") != "done" else j for j in jobs])
    return len(moved)


def rows(store) -> dict:
    data = store.read("ct_connections") or {}
    if _needs_migration(data):
        migrate(store)
        data = store.read("ct_connections") or {}
    return data


# ---------------------------------------------------------------- reading
def all_links(store) -> list:
    return sorted((l for l in rows(store).values() if isinstance(l, dict) and l.get("id")), key=order)


def order(link: dict) -> tuple:
    """Oldest first: creation time, then the creation counter (two links made in the same second)."""
    return (link.get("created_at") or "", link.get("seq") or 0, link["id"])


def links_of(store, ws: str) -> list:
    from .workspaces import MAIN
    return [l for l in all_links(store) if l.get("workspace") == (ws or MAIN)]


def get(store, link_id: str) -> Optional[dict]:
    link = rows(store).get(str(link_id or "")[:40])
    return link if isinstance(link, dict) else None


def is_active(link: Optional[dict]) -> bool:
    return bool(link and link.get("enabled", True) and link.get("base_url") and link.get("api_key"))


def is_personal(link: Optional[dict]) -> bool:
    return bool(link) and link.get("dh_scope") == "member"


def member_email(link: Optional[dict]) -> str:
    return str(((link or {}).get("dh_member") or {}).get("email") or "").strip().lower()


def dh_id(link: Optional[dict]) -> str:
    return str(((link or {}).get("dh_workspace") or {}).get("id") or "")


def shadowed(link: dict, among: list) -> bool:
    """A personal link loses to a workspace link (in `among`) to the same DialHalo workspace."""
    return is_personal(link) and bool(dh_id(link)) and any(
        not is_personal(o) and o["id"] != link["id"] and dh_id(o) == dh_id(link) for o in among)


def siblings(store, link: dict) -> set:
    """Ids of the links of this Outreach workspace that see the same DialHalo workspace (this one included)."""
    if not dh_id(link):
        return {link["id"]}
    return {l["id"] for l in links_of(store, link.get("workspace")) if dh_id(l) == dh_id(link)} | {link["id"]}


def active_links(store, ws: str) -> list:
    return [l for l in links_of(store, ws) if is_active(l)]


def primary_of(links: list) -> Optional[dict]:
    return next((l for l in links if l.get("primary")), None) or (links[0] if links else None)


def primary(store, ws: str) -> Optional[dict]:
    return primary_of(links_of(store, ws))


def primary_active(store, ws: str) -> Optional[dict]:
    return primary_of(active_links(store, ws))


def resolve(store, target) -> Optional[dict]:
    """A link dict, a link id, or a workspace id (its primary active link)."""
    if isinstance(target, dict):
        return target
    return get(store, target) or primary_active(store, target)


# ---------------------------------------------------------------- writing
def save(store, ws: str, link_id: Optional[str], changes: dict, who: str, new_id: Optional[str] = None) -> dict:
    """Create (link_id None) or change a link. Secrets are kept when not sent again. The first link is the primary."""
    ct = _ct()
    stamp = ct.iso(ct.now_utc())
    out = {}

    def apply(data: dict) -> dict:
        cur = dict(data.get(link_id) or {}) if link_id else {}
        if link_id and (not cur or cur.get("workspace") != ws):
            raise KeyError(link_id)
        if not cur:
            first = not any(isinstance(l, dict) and l.get("workspace") == ws for l in data.values())
            seq = 1 + max([l.get("seq") or 0 for l in data.values() if isinstance(l, dict)] or [0])
            cur = {"id": new_id or uuid.uuid4().hex[:20], "workspace": ws, "created_at": stamp, "created_by": who, "enabled": True, "seq": seq,
                   "lead_sync": "call_tasks", "primary": first, "via": "manual"}
        for key in EDITABLE:
            if key in changes and changes[key] is not None:
                cur[key] = changes[key]
        if is_personal(cur) and cur.get("lead_sync") not in PERSONAL_SYNC:
            cur["lead_sync"] = "owners_members"        # personal: only leads owned by the paired Outreach person
        cur.update(updated_at=stamp, updated_by=who)
        out.update(cur)
        return {**data, cur["id"]: cur}
    rows(store)                                        # migrate first
    store.update("ct_connections", apply)
    return dict(out)


def set_primary(store, link_id: str) -> None:
    link = get(store, link_id)
    if not link:
        return
    ws = link["workspace"]
    store.update("ct_connections", lambda data: {k: ({**v, "primary": k == link_id} if isinstance(v, dict) and v.get("workspace") == ws else v)
                                                 for k, v in data.items()})


def delete(store, link_id: str) -> Optional[dict]:
    """Remove one link, its unsent jobs and its backfill state. Returns the removed link (or None)."""
    gone = {}

    def apply(data: dict) -> dict:
        gone.update(data.get(link_id) or {})
        rest = {k: v for k, v in data.items() if k != link_id}
        ws = gone.get("workspace")
        if gone.get("primary") and ws:                 # the oldest remaining link of the workspace becomes the primary
            left = sorted((v for v in rest.values() if isinstance(v, dict) and v.get("workspace") == ws and v.get("id")), key=order)
            if left:
                rest[left[0]["id"]] = {**left[0], "primary": True}
        return rest
    rows(store)
    store.update("ct_connections", apply)
    if not gone:
        return None
    ws = gone.get("workspace")
    store.update("ct_outbox", lambda jobs: [j for j in jobs if j.get("link") != link_id or j.get("status") == "done"])
    store.update("ct_backfill", lambda data: {k: v for k, v in (data or {}).items() if k != link_id})
    from . import calltracker_match
    if not links_of(store, ws):
        store.update("ct_outbox", lambda jobs: [j for j in jobs if j.get("ws") != ws or j.get("status") == "done"])
        calltracker_match.forget_workspace(store, ws)
    else:
        calltracker_match.forget_link(store, ws, link_id)
    return gone


def delete_workspace(store, ws: str) -> bool:
    ids = [l["id"] for l in links_of(store, ws)]
    for link_id in ids:
        delete(store, link_id)
    from . import calltracker_match
    calltracker_match.forget_workspace(store, ws)
    return bool(ids)


def note(store, link_id: str, key: str, ok: bool, detail: str = "") -> None:
    """last_push / last_event / last_test / last_match on the link (our own wording only, never secrets)."""
    ct = _ct()
    row = {"at": ct.iso(ct.now_utc()), "ok": bool(ok), "detail": str(detail or "")[:300]}
    store.update("ct_connections", lambda data: {**data, link_id: {**data[link_id], key: row}} if link_id in data else data)


def note_auth(store, link_id: str, failed: bool, detail: str = "", code: str = "") -> None:
    """DialHalo refused this link's key (401 / 403) on a test, a send or a call fetch: the card says "Not authorised —
    reconnect" until a later test or send succeeds. Writes only when the state changes.
    Personal keys: code connection_paused -> "Paused in DialHalo" (paused_in_dh; not broken, cleared by a later success);
    code personal_connection -> the last thing DialHalo refused to a personal key (personal_refused); the key is fine."""
    ct = _ct()
    cur = get(store, link_id)
    if not cur:
        return
    if failed and code in ("connection_paused", "personal_connection"):
        key = "paused_in_dh" if code == "connection_paused" else "personal_refused"
        row = {"at": ct.iso(ct.now_utc()), "detail": str(detail or "")[:300]}
        store.update("ct_connections", lambda data: {**data, link_id: {**data[link_id], key: row}} if link_id in data else data)
        return
    if not failed and cur.get("paused_in_dh"):
        store.update("ct_connections", lambda data: {**data, link_id: {k: v for k, v in data[link_id].items() if k != "paused_in_dh"}}
                     if link_id in data else data)
        cur = get(store, link_id) or cur
        resumed(store, link_id)
    if bool(cur.get("auth_failed")) == bool(failed):
        return
    row = {"at": ct.iso(ct.now_utc()), "detail": str(detail or "")[:300]} if failed else None

    def apply(data: dict) -> dict:
        if link_id not in data:
            return data
        link = {k: v for k, v in data[link_id].items() if k != "auth_failed"}
        return {**data, link_id: {**link, **({"auth_failed": row} if row else {})}}
    store.update("ct_connections", apply)


def resumed(store, link_id: str) -> None:
    """A paused link works again: reconcile its call feed (GET /sync/calls from its cursor) so calls made during the pause
    are recorded even if a delivery was lost. Idempotent with the webhooks DialHalo held (one row per call). In the
    background worker's thread when the worker runs, at once otherwise (tests). Never raises."""
    ct = _ct()

    def run():
        try:
            from . import calltracker_kit
            link = get(store, link_id)
            if is_active(link):
                calltracker_kit.reconcile(store, link)
        except Exception as exc:
            print(f"calltracker reconcile after pause failed: {type(exc).__name__}")
    if ct.KICK:
        import threading
        threading.Thread(target=run, daemon=True, name="calltracker-resume").start()
    else:
        run()


OUTCOMES_TTL_SECONDS = 24 * 3600


def outcomes_of(store, link: dict, refresh: bool = False) -> list:
    """DialHalo's outcome list for this link (GET /api/v1/outcomes -> [{id, name, active}]), cached a day on the link
    (dh_outcomes {at, rows}); refreshed on Test. An older DialHalo (404) caches an empty list; other errors keep the cache."""
    ct = _ct()
    cache = link.get("dh_outcomes") or {}
    at = ct.parse(cache.get("at"))
    fresh = at and (ct.now_utc() - at).total_seconds() < OUTCOMES_TTL_SECONDS
    if (refresh or not fresh) and is_active(link):
        try:
            got = ct.ct_request(link, "GET", "/api/v1/outcomes")
        except ct.CallTrackerError as exc:
            if exc.status != 404:
                return list(cache.get("rows") or [])
            got = []
        rows_ = [{"id": r.get("id"), "name": str(r.get("name") or "")[:80], "active": bool(r.get("active", True))}
                 for r in (got if isinstance(got, list) else []) if isinstance(r, dict) and r.get("name")][:200]
        row = {"at": ct.iso(ct.now_utc()), "rows": rows_}
        store.update("ct_connections", lambda data: {**data, link["id"]: {**data[link["id"]], "dh_outcomes": row}} if link["id"] in data else data)
        return rows_
    return list(cache.get("rows") or [])


def dialhalo_name(store, link: dict, key: str) -> str:
    """The real DialHalo outcome name for a mapping key (keys are lower case), matched case-insensitively with collapsed
    whitespace; an active outcome first. Falls back to the key."""
    norm = lambda v: " ".join(str(v or "").lower().split())
    rows_ = outcomes_of(store, link)
    hits = [r for r in rows_ if norm(r.get("name")) == norm(key)]
    hits.sort(key=lambda r: not r.get("active"))
    return hits[0]["name"] if hits else key


def note_unmapped(store, link_id: str, name: str, counted_as: str) -> None:
    """A DialHalo outcome name with no row in the mapping was counted as `counted_as`: the card says so."""
    ct = _ct()
    key = " ".join(name.lower().split())[:80]
    stamp = ct.iso(ct.now_utc())

    def apply(data: dict) -> dict:
        link = data.get(link_id)
        if not link:
            return data
        seen = dict(link.get("unmapped_outcomes") or {})
        cur = seen.get(key) or {"name": name[:80], "count": 0}
        seen[key] = {**cur, "count": cur["count"] + 1, "last_at": stamp, "counted_as": counted_as}
        if len(seen) > 30:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1].get("last_at", ""))[-30:])
        return {**data, link_id: {**link, "unmapped_outcomes": seen}}
    store.update("ct_connections", apply)


def note_unmapped_out(store, link_id: str, name: str, why: str) -> None:
    """An Outreach outcome went to DialHalo as a note only (app/calltracker_outcomes.py): `why` is "no_rule" (no mapping
    row gives it a DialHalo name) or "unknown_in_dialhalo" (the mapped name is not an outcome in that DialHalo)."""
    ct = _ct()
    key = " ".join(str(name or "").lower().split())[:80]
    if not key:
        return
    stamp = ct.iso(ct.now_utc())

    def apply(data: dict) -> dict:
        link = data.get(link_id)
        if not link:
            return data
        seen = dict(link.get("unmapped_outgoing") or {})
        cur = seen.get(key) or {"name": str(name)[:80], "count": 0}
        seen[key] = {**cur, "count": cur["count"] + 1, "last_at": stamp, "why": why}
        if len(seen) > 30:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1].get("last_at", ""))[-30:])
        return {**data, link_id: {**link, "unmapped_outgoing": seen}}
    store.update("ct_connections", apply)


# ---------------------------------------------------------------- members (people pairing)
def save_members(store, link_id: str, members: list) -> list:
    ct = _ct()
    clean = [{"name": str(m.get("name") or "")[:120], "email": str(m.get("email") or "").lower()[:190], "active": 1 if m.get("active", 1) else 0}
             for m in members if isinstance(m, dict) and m.get("email")][:1000]
    row = {"at": ct.iso(ct.now_utc()), "rows": clean}
    store.update("ct_connections", lambda data: {**data, link_id: {**data[link_id], "members": row}} if link_id in data else data)
    return clean


def members_of(store, link: dict, refresh: bool = False) -> list:
    """The DialHalo members of this link (cached for MEMBERS_TTL_SECONDS). Network errors leave the cache as it is."""
    ct = _ct()
    cache = link.get("members") or {}
    at = ct.parse(cache.get("at"))
    fresh = at and (ct.now_utc() - at).total_seconds() < MEMBERS_TTL_SECONDS
    if (refresh or not fresh) and is_active(link):
        try:
            got = ct.ct_request(link, "GET", "/api/v1/members")
            return save_members(store, link["id"], got if isinstance(got, list) else [])
        except ct.CallTrackerError:
            pass
    return list(cache.get("rows") or [])


def member_emails(link: dict) -> set:
    return {m["email"] for m in ((link.get("members") or {}).get("rows") or []) if m.get("active", 1) and m.get("email")}


def pairing(store, link: dict) -> dict:
    """People matched by email, from the cached members list (no network): N of M Outreach people, with names."""
    rows_ = (link.get("members") or {}).get("rows") or []
    if not (link.get("members") or {}).get("at"):
        return {"checked": False, "matched": 0, "total": 0, "people": []}
    active = [m for m in rows_ if m.get("active", 1)]
    people = _ct().owner_preview(store, link["workspace"], active, rows_)
    return {"checked": True, "at": link["members"]["at"], "matched": sum(p["matched"] for p in people), "total": len(people),
            "members": len(active), "people": [{"name": p["name"], "matched": p["matched"], "member": p.get("calltracker") or ""} for p in people]}


# ---------------------------------------------------------------- which link a lead is pushed to
def owner_emails(store, lead: dict, task: Optional[dict] = None, memo: Optional[dict] = None) -> set:
    """Emails of the lead's owner and the task's owner. `memo` (name -> email) saves a users read per lead in a sweep."""
    ct = _ct()
    memo = {} if memo is None else memo
    out = set()
    for who in (lead.get("owner"), (task or {}).get("owner")):
        key = str(who or "")
        if key not in memo:
            memo[key] = ct.email_of_person(store, who)
        if memo[key]:
            out.add(memo[key])
    return out


def paired(store, link: dict, lead: dict, task: Optional[dict] = None, memo: Optional[dict] = None, extra: Optional[set] = None) -> bool:
    """A personal link's member is the lead's owner / the task's owner (or one of `extra` e-mails), by e-mail."""
    email = member_email(link)
    return bool(email) and email in (owner_emails(store, lead, task, memo) | set(extra or ()))


def qualifies(store, link: dict, lead: dict, call_task: bool = False, task: Optional[dict] = None, memo: Optional[dict] = None) -> bool:
    mode = link.get("lead_sync") or "call_tasks"
    if is_personal(link):
        if mode == "off" or not paired(store, link, lead, task, memo):
            return False
        return call_task if mode == "call_tasks" else True
    if mode == "call_tasks":
        return call_task
    if mode == "owners_members":
        return bool(owner_emails(store, lead, task, memo) & member_emails(link))
    if mode == "all_with_phone":
        ct = _ct()
        return any(ct.norm_phone(p) for p in [lead.get("phone"), *(lead.get("phones") or [])])
    return False


def pushed_link(store, lead: dict, links: list) -> Optional[str]:
    """The link this lead was already pushed to (a lead pushed before the migration belongs to the legacy link)."""
    info = lead.get("calltracker") or {}
    if info.get("link"):
        return info["link"]
    if info.get("lead_id"):
        return next((l["id"] for l in links if l.get("legacy")), None)
    return None


def route(store, lead: dict, call_task: bool = False, task: Optional[dict] = None, links: Optional[list] = None,
          memo: Optional[dict] = None) -> Optional[dict]:
    """The one link a lead is pushed to, or None (see the module doc)."""
    from .workspaces import ws_of
    links = active_links(store, ws_of(lead)) if links is None else links
    cands = [l for l in links if qualifies(store, l, lead, call_task, task, memo) and not shadowed(l, links)]
    if not cands:
        return None
    sticky = pushed_link(store, lead, links)
    return next((l for l in cands if l["id"] == sticky), None) or primary_of(cands)      # cands are oldest first


# ---------------------------------------------------------------- the card
def public_view(store, link: dict) -> dict:
    ct = _ct()
    key = str(link.get("api_key") or "")
    from . import calltracker_backfill as backfill
    return {"id": link["id"], "workspace": link["workspace"], "base_url": link.get("base_url", ""), "public_url": link.get("public_url", ""),
            "enabled": link.get("enabled", True), "api_key_set": bool(key), "api_key_hint": ("••••" + key[-4:]) if len(key) > 8 else ("set" if key else ""),
            "webhook_secret_set": bool(link.get("webhook_secret")), "mapping": ct.mapping_of(link), "mapping_custom": bool(link.get("mapping")),
            "via": link.get("via") or "manual", "dh_workspace": link.get("dh_workspace") or None, "lead_sync": link.get("lead_sync") or "call_tasks",
            "primary": bool(link.get("primary")), "pairing": pairing(store, link), "auth_failed": link.get("auth_failed") or None,
            "paused_in_dh": link.get("paused_in_dh") or None, "personal_refused": link.get("personal_refused") or None,
            "dh_scope": "member" if is_personal(link) else "workspace", "dh_member": link.get("dh_member") if is_personal(link) else None,
            "backfill": backfill.public(backfill.state(store, link["id"])),
            "last_push": link.get("last_push"), "last_event": link.get("last_event"), "last_test": link.get("last_test"), "last_match": link.get("last_match"),
            "last_reconcile": link.get("last_reconcile"), "last_reconcile_status": link.get("last_reconcile_status"),
            "unmapped_outcomes": sorted((link.get("unmapped_outcomes") or {}).values(), key=lambda r: -r.get("count", 0)),
            "unmapped_outgoing": sorted((link.get("unmapped_outgoing") or {}).values(), key=lambda r: -r.get("count", 0)),
            "last_outcome": link.get("last_outcome"),
            "created_at": link.get("created_at"), "updated_at": link.get("updated_at")}
