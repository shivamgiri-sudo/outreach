"""Who gets a new lead: ordered rules, then round-robin inside the matching team, else the default team, else nobody.

Everything is per workspace and OFF by default (settings["assignment"][ws]["on"]), so nothing changes until an admin
turns it on. A lead that already has an owner is never re-owned. A human member who uploads a lead owns it.

Stored under settings["assignment"][ws] = {on, teams, rules, first_touch, pointers: {team_id: next index}}.
Lead fields written: owner (via lead_ops), owner_why {kind: rule|upload, rule_id, at, by, team}, first_touch_due_at None.
Stats read lead["reassign_log"] = [{from, to, at}] and lead["reassigns"] (written by the first-touch clock).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import auth, lead_ops
from . import workspaces as workspaces_mod
from .lead_model import event, now_iso

log = logging.getLogger(__name__)
KEY = "assignment"
MAX_TEAMS, MAX_MEMBERS, MAX_RULES = 30, 100, 200
WHEN_KEYS = ("source", "city", "branch", "campaign", "tag")
FIRST_TOUCH = {"alert_minutes": 30, "reassign_minutes": 120, "alert": True, "reassign": True}


class AssignmentRefused(ValueError):
    """The saved settings are not valid; the message says why in plain words."""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _low(value) -> str:
    return " ".join(str(value or "").split()).lower()


# ---------- settings ----------
def _raw(store, ws: str) -> dict:
    return (store.read("settings").get(KEY) or {}).get(ws or workspaces_mod.MAIN) or {}


def config(store, ws: str) -> dict:
    raw = _raw(store, ws)
    ft = {**FIRST_TOUCH, **(raw.get("first_touch") or {})}
    return {"on": bool(raw.get("on")),
            "teams": [{"id": t["id"], "name": t.get("name", ""), "members": list(t.get("members") or []),
                       "default": bool(t.get("default")), "daily_cap": int(t.get("daily_cap") or 0)} for t in raw.get("teams") or []],
            "rules": [{"id": r["id"], "when": {k: (r.get("when") or {}).get(k, "") for k in WHEN_KEYS}, "team": r.get("team", "")}
                      for r in raw.get("rules") or []],
            "first_touch": ft}


def _int(value, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AssignmentRefused(f"{what} must be a number.")
    try:
        n = int(float(value))
    except ValueError:
        raise AssignmentRefused(f"{what} must be a number.") from None
    if not low <= n <= high:
        raise AssignmentRefused(f"{what} must be between {low} and {high}.")
    return n


def _known_names(store) -> dict:
    return {n.lower(): n for n in lead_ops.owner_choices(store)}


def save_config(store, ws: str, body: dict) -> dict:
    """Validate and fully replace this workspace's settings (pointers are kept). Raises AssignmentRefused."""
    if not isinstance(body, dict):
        raise AssignmentRefused("Send the assignment settings as an object.")
    known = _known_names(store)
    teams, ids = [], set()
    raw_teams = body.get("teams") or []
    if not isinstance(raw_teams, list) or len(raw_teams) > MAX_TEAMS:
        raise AssignmentRefused(f"At most {MAX_TEAMS} teams.")
    for t in raw_teams:
        if not isinstance(t, dict):
            raise AssignmentRefused("Each team must be an object.")
        name = " ".join(str(t.get("name") or "").split())[:60]
        if not name:
            raise AssignmentRefused("Every team needs a name.")
        tid = str(t.get("id") or "").strip()[:40] or uuid.uuid4().hex[:8]
        if tid in ids:
            raise AssignmentRefused(f"Team id {tid} is used twice.")
        ids.add(tid)
        members = []
        if not isinstance(t.get("members") or [], list) or len(t.get("members") or []) > MAX_MEMBERS:
            raise AssignmentRefused(f"At most {MAX_MEMBERS} members per team.")
        for m in t.get("members") or []:
            canon = known.get(_low(m))
            if canon is None:
                raise AssignmentRefused(f"{' '.join(str(m).split())[:80] or 'That name'} is not in the team list. Add them under Team first.")
            if canon not in members:
                members.append(canon)
        teams.append({"id": tid, "name": name, "members": members, "default": bool(t.get("default")),
                      "daily_cap": _int(t.get("daily_cap") or 0, 0, 10000, f"Daily cap of {name}")})
    if sum(1 for t in teams if t["default"]) > 1:
        raise AssignmentRefused("Only one team can be the default team.")
    rules, rule_ids = [], set()
    raw_rules = body.get("rules") or []
    if not isinstance(raw_rules, list) or len(raw_rules) > MAX_RULES:
        raise AssignmentRefused(f"At most {MAX_RULES} rules.")
    for r in raw_rules:
        if not isinstance(r, dict):
            raise AssignmentRefused("Each rule must be an object.")
        when = {k: " ".join(str((r.get("when") or {}).get(k) or "").split())[:120] for k in WHEN_KEYS}
        if not any(when.values()):
            raise AssignmentRefused("Every rule needs at least one condition (source, city, branch, campaign or tag).")
        if r.get("team") not in ids:
            raise AssignmentRefused("A rule points to a team that does not exist.")
        rid = str(r.get("id") or "").strip()[:40] or uuid.uuid4().hex[:8]
        if rid in rule_ids:
            raise AssignmentRefused(f"Rule id {rid} is used twice.")
        rule_ids.add(rid)
        rules.append({"id": rid, "when": when, "team": r["team"]})
    ft_in = body.get("first_touch") or {}
    if not isinstance(ft_in, dict):
        raise AssignmentRefused("first_touch must be an object.")
    ft = {**FIRST_TOUCH, **{k: ft_in[k] for k in FIRST_TOUCH if k in ft_in}}
    ft["alert_minutes"] = _int(ft["alert_minutes"], 1, 10080, "Alert minutes")
    ft["reassign_minutes"] = _int(ft["reassign_minutes"], 1, 10080, "Reassign minutes")
    if ft["reassign_minutes"] < ft["alert_minutes"]:
        raise AssignmentRefused("Reassign minutes cannot be less than alert minutes.")
    ft["alert"], ft["reassign"] = bool(ft["alert"]), bool(ft["reassign"])
    on = bool(body.get("on"))
    key = ws or workspaces_mod.MAIN

    def apply(s: dict) -> dict:
        all_ws = dict(s.get(KEY) or {})
        old = all_ws.get(key) or {}
        pointers = {t: p for t, p in (old.get("pointers") or {}).items() if t in ids}
        all_ws[key] = {"on": on, "teams": teams, "rules": rules, "first_touch": ft, "pointers": pointers}
        return {**s, KEY: all_ws}
    store.update("settings", apply)
    return config(store, key)


def is_on(store, ws: str) -> bool:
    return bool(_raw(store, ws).get("on"))


# ---------- people ----------
def _login_for(users: list, name: str) -> Optional[dict]:
    n = _low(name)
    return next((u for u in users if n in {_low(u.get("name")), _low(u.get("email"))}), None)


def _unavailable(user: Optional[dict], ws: str) -> bool:
    if user is None:
        return False                       # a team-list name with no login of its own
    if user.get("disabled") or user.get("on_leave") or _low(user.get("status")) in ("disabled", "inactive", "suspended"):
        return True
    wss = user.get("workspaces") or []
    return user.get("role") != "owner" and bool(wss) and ws not in wss


def team_of_owner(store, ws: str, owner_name: str) -> str:
    n = _low(owner_name)
    if not n:
        return ""
    users = auth.users(store)
    login = _login_for(users, owner_name)
    names = {n} | ({_low(login.get("name")), _low(login.get("email"))} - {""} if login else set())
    for t in config(store, ws)["teams"]:
        if names & {_low(m) for m in t["members"]}:
            return t["id"]
    return ""


def manager_of(store, ws: str, owner_name: str = "") -> str:
    """An admin of this workspace to tell (never the owner themself), else an owner login, else ''."""
    me = _low(owner_name)
    users = [u for u in auth.users(store) if not (u.get("disabled") or u.get("on_leave"))]

    def pick(rows):
        for u in rows:
            if me and me in {_low(u.get("name")), _low(u.get("email"))}:
                continue
            return u.get("name") or u.get("email") or ""
        return ""
    return pick([u for u in users if u.get("role") == "admin" and ws in (u.get("workspaces") or [])]) \
        or pick([u for u in users if u.get("role") == "owner"])


class _Picker:
    """Round-robin state for one workspace, loaded once so a big batch does not re-read the store per lead."""

    def __init__(self, store, ws: str):
        self.store, self.ws = store, ws
        self.cfg = config(store, ws)
        self.teams = {t["id"]: t for t in self.cfg["teams"]}
        self.pointers = dict(_raw(store, ws).get("pointers") or {})
        self.users = auth.users(store)
        self.campaigns = {c.get("id"): c.get("name") for c in store.read("campaigns") if isinstance(c, dict)} if self.cfg["rules"] else {}
        self.today = None                  # lower owner name -> leads given to them today (loaded on first need)
        self.dirty = False

    def _counts(self) -> dict:
        if self.today is None:
            day, out = _today(), {}
            for l in self.store.read("leads"):
                if workspaces_mod.ws_of(l) == self.ws and l.get("owner") and str((l.get("owner_why") or {}).get("at") or "").startswith(day):
                    out[_low(l["owner"])] = out.get(_low(l["owner"]), 0) + 1
            self.today = out
        return self.today

    def next(self, team_id: str, exclude=()) -> str:
        team = self.teams.get(team_id)
        if not team or not team["members"]:
            return ""
        skip = {_low(x) for x in exclude}
        members, size = team["members"], len(team["members"])
        start = int(self.pointers.get(team_id) or 0) % size
        for step in range(size):
            idx = (start + step) % size
            name = members[idx]
            login = _login_for(self.users, name)
            shown = (login.get("name") or login.get("email")) if login else name
            if _low(name) in skip or _low(shown) in skip or _unavailable(login, self.ws):
                continue
            cap = team["daily_cap"]
            if cap and self._counts().get(_low(shown), 0) >= cap:
                continue
            self.pointers[team_id] = (idx + 1) % size
            self.dirty = True
            if cap:
                self._counts()[_low(shown)] = self._counts().get(_low(shown), 0) + 1
            return shown
        return ""

    def flush(self) -> None:
        if not self.dirty:
            return
        key, pointers = self.ws, dict(self.pointers)

        def apply(s: dict) -> dict:
            all_ws = dict(s.get(KEY) or {})
            cur = dict(all_ws.get(key) or {})
            cur["pointers"] = {**(cur.get("pointers") or {}), **pointers}
            all_ws[key] = cur
            return {**s, KEY: all_ws}
        self.store.update("settings", apply)
        self.dirty = False


def next_member(store, ws: str, team_id: str, exclude=()) -> str:
    """The next person in a team, rotating the pointer; '' when nobody can take a lead now."""
    p = _Picker(store, ws)
    name = p.next(team_id, exclude)
    p.flush()
    return name


# ---------- matching ----------
def _lead_values(lead: dict, campaign_names: Optional[dict] = None) -> dict:
    cf = lead.get("custom_fields") or {}
    sources = {_low(lead.get("source")), _low(lead.get("source_id"))} | {_low(s.get("id")) for s in lead.get("sources") or [] if isinstance(s, dict)}
    campaign = {_low(lead.get(k)) for k in ("campaign", "campaign_id", "campaign_name", "capture_connector_id")} | {_low(cf.get("campaign"))}
    campaign |= {_low((campaign_names or {}).get(lead.get("campaign_id")))}
    return {"source": sources - {""}, "city": {_low(lead.get("city"))} - {""},
            "branch": ({_low(lead.get("branch"))} | {_low(cf.get("branch"))}) - {""},
            "campaign": campaign - {""}, "tag": {_low(t) for t in lead.get("tags") or []} - {""}}


def _matches(rule: dict, values: dict) -> bool:
    cond = {k: _low(v) for k, v in (rule.get("when") or {}).items() if _low(v)}
    return bool(cond) and all(v in values[k] for k, v in cond.items())


def _decide(picker: _Picker, lead: dict, uploader) -> tuple:
    """(owner, why dict) or ('', {})."""
    if isinstance(uploader, dict):
        name = uploader.get("name") or uploader.get("email") or ""
        member = uploader.get("role") == "caller"
    else:
        name, member = str(uploader or ""), bool(uploader)
    if member and name:
        return name, {"kind": "upload", "rule_id": "", "by": name}
    values = _lead_values(lead, picker.campaigns)
    for rule in picker.cfg["rules"]:
        if rule["team"] in picker.teams and _matches(rule, values):
            who = picker.next(rule["team"])
            return (who, {"kind": "rule", "rule_id": rule["id"], "team": rule["team"]}) if who else ("", {})
    default = next((t for t in picker.cfg["teams"] if t["default"]), None)
    if default:
        who = picker.next(default["id"])
        return (who, {"kind": "rule", "rule_id": "default", "team": default["id"]}) if who else ("", {})
    return "", {}


def _give(store, lead_id: str, owner: str, why: dict) -> Optional[dict]:
    """Atomically give an UNOWNED lead its owner; None when it already has one (never re-owned) or is gone."""
    stamp = now_iso()
    detail = {"upload": "uploaded by", "rule": "rule " + str(why.get("rule_id") or "") + ", round-robin"}[why["kind"]]
    full = {"kind": why["kind"], "rule_id": why.get("rule_id", ""), "at": stamp, "by": why.get("by") or "system",
            **({"team": why["team"]} if why.get("team") else {})}

    def change(lead: dict):
        if lead.get("owner"):
            return None
        changed = lead_ops._owner_change(lead, owner, full["by"], detail)
        return {**changed, "owner_why": full, "first_touch_due_at": None} if changed else None
    saved = store.update_lead(lead_id, change)
    return saved if saved is not None and (saved.get("owner_why") or {}).get("at") == stamp else None


def _start_clock(store, lead_id: str) -> None:
    try:
        from . import first_touch
    except ImportError:
        return
    try:
        first_touch.start(store, lead_id)
    except Exception:                      # the clock must never break capturing a lead
        log.exception("first_touch.start failed for %s", lead_id)


def _assign(store, picker: _Picker, lead: dict, uploader) -> str:
    if lead.get("owner"):
        return lead["owner"]
    owner, why = _decide(picker, lead, uploader)
    if not owner:
        return ""
    saved = _give(store, lead["id"], owner, why)
    if saved is None:                      # another writer owned it first: keep theirs
        now = next((l for l in store.read("leads") if l["id"] == lead["id"]), None) or {}
        return now.get("owner") or ""
    _start_clock(store, lead["id"])
    return owner


def assign_new(store, lead: dict, uploader=None) -> str:
    """Owner of a new lead: keeps an existing owner; a member who uploaded it; else the first matching rule's team
    (round-robin), else the default team, else '' (unassigned). Does nothing while assignment is off for the workspace."""
    if lead.get("owner") or not lead.get("id"):
        return lead.get("owner") or ""
    ws = workspaces_mod.ws_of(lead)
    if not is_on(store, ws):
        return ""
    picker = _Picker(store, ws)
    owner = _assign(store, picker, lead, uploader)
    picker.flush()
    return owner


def assign_new_many(store, leads: list, uploader=None) -> int:
    """assign_new for a batch (one round-robin state per workspace). Returns how many got an owner."""
    pickers, done = {}, 0
    for lead in leads:
        ws = workspaces_mod.ws_of(lead)
        if lead.get("owner") or not lead.get("id") or not is_on(store, ws):
            continue
        picker = pickers.setdefault(ws, _Picker(store, ws))
        done += 1 if _assign(store, picker, lead, uploader) else 0
    for p in pickers.values():
        p.flush()
    return done


def run_unowned(store, ws: str) -> dict:
    """Give every unowned lead of this workspace an owner by the rules. {assigned, left}."""
    unowned = [l for l in store.read("leads") if workspaces_mod.ws_of(l) == ws and not l.get("owner")]
    if not is_on(store, ws):
        return {"assigned": 0, "left": len(unowned)}
    picker, assigned = _Picker(store, ws), 0
    for lead in unowned:
        assigned += 1 if _assign(store, picker, lead, None) else 0
    picker.flush()
    return {"assigned": assigned, "left": len(unowned) - assigned}


def stats(store, ws: str) -> dict:
    cfg = config(store, ws)
    names = []
    for t in cfg["teams"]:
        for m in t["members"]:
            if m not in names:
                names.append(m)
    rows = {_low(n): {"name": n, "assigned": 0, "_mins": [], "reassigned_away": 0} for n in names}
    unassigned = reassigned = 0
    for l in store.read("leads"):
        if workspaces_mod.ws_of(l) != ws:
            continue
        owner = _low(l.get("owner"))
        if not owner:
            unassigned += 1
        elif owner in rows:
            row = rows[owner]
            row["assigned"] += 1
            start, end = (l.get("owner_why") or {}).get("at"), l.get("first_touch_at")
            if start and end:
                try:
                    row["_mins"].append(max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60))
                except ValueError:
                    pass
        reassigned += int(l.get("reassigns") or 0)
        for step in l.get("reassign_log") or []:
            if isinstance(step, dict) and _low(step.get("from")) in rows:
                rows[_low(step["from"])]["reassigned_away"] += 1
    members = [{"name": r["name"], "assigned": r["assigned"],
                "avg_first_touch_min": round(sum(r["_mins"]) / len(r["_mins"]), 1) if r["_mins"] else None,
                "reassigned_away": r["reassigned_away"]} for r in rows.values()]
    return {"members": members, "unassigned": unassigned, "reassigned_total": reassigned}
