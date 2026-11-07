"""Companies (accounts): every contact at one employer seen together.

Leads are grouped by their business domain (acme.com). A lead with no business domain joins the domain group whose
company name matches, else it is grouped by its lower-cased company name. Free-mail addresses (gmail.com and the
like) never identify a company, so two strangers on gmail are never put together; a lead with neither a business
domain nor a company name stands alone.

Rollup rules: the company's stage is the furthest stage any contact reached; it is an "engaged account" once anyone
replied (a person, not an out-of-office) and not everyone there has left (not now, unsubscribed, disqualified); and it warns when two contacts are both in an active sequence and were touched (or enrolled) on the same IST
day, so one employer is not hit twice in a day.
"""
import re
from datetime import datetime, timezone
from typing import Optional

from . import company_health, workspaces
from .activities import _normalise_task, lead_activity
from .hygiene import business_domain
from .jobs import _shared as is_shared_host
from .replies import is_person_reply
from .scoring import score_lead
from .sequences import IST
from .sheet_import import company_match_key
from .standardise import NOT_COMPANY_SITES, registrable_domain
from .stages import EXIT_STAGES, STAGES

SORTS = ("last_touch", "contacts", "score", "health", "name")
MAX_LIMIT = 200
MAX_TIMELINE = 500
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def parse_when(raw) -> Optional[datetime]:
    """Any ISO stamp (Z, +05:30, no zone = UTC) as an aware datetime, or None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def iso_utc(when: Optional[datetime]) -> Optional[str]:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds") if when else None


def ist_day(raw) -> Optional[str]:
    when = parse_when(raw)
    return when.astimezone(IST).date().isoformat() if when else None


def lead_domain(lead: dict) -> Optional[str]:
    """The employer's own domain, reduced to its registrable part (careers.acme.com and mail.acme.com are acme.com).
    Job boards and social sites (a LinkedIn page given as the website) never identify a company."""
    for value in (lead.get("domain"), lead.get("website"), lead.get("email")):
        domain = business_domain(value)
        if not domain or is_shared_host(domain):
            continue
        root = registrable_domain(domain) or domain
        if root in NOT_COMPANY_SITES:
            continue
        return root
    return None


def norm_name(name) -> str:
    return " ".join(str(name or "").lower().split())


def natural_key(name) -> tuple:
    """Sort key for names: case-insensitive, and numbers by value ("Store 2" before "Store 10")."""
    parts = re.split(r"(\d+)", norm_name(name).casefold())
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p)


def name_key(name) -> str:
    """Company name for grouping: 'Acme Pvt. Ltd.' and 'ACME' are the same employer."""
    return company_match_key(str(name or "")) or norm_name(name)


def group_keys(leads: list) -> dict:
    """lead id -> company key ("d:acme.com", "n:acme", or "p:<lead id>" for a lone person)."""
    by_name = {}
    for l in leads:
        domain, name = lead_domain(l), name_key(l.get("company"))
        if domain and name:
            by_name.setdefault(name, set()).add(domain)
    keys = {}
    for l in leads:
        domain, name = lead_domain(l), name_key(l.get("company"))
        if domain:
            keys[l["id"]] = f"d:{domain}"
        elif name and len(by_name.get(name, ())) == 1:
            keys[l["id"]] = f"d:{next(iter(by_name[name]))}"
        elif name:
            keys[l["id"]] = f"n:{name}"
        else:
            keys[l["id"]] = f"p:{l['id']}"
    return keys


def stage_rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else -1


def best_stage(stages: list) -> str:
    live = [s for s in stages if s in STAGES]
    if live:
        return max(live, key=stage_rank)
    for s in ("not_now", "disqualified", "unsubscribed"):
        if s in stages:
            return s
    return stages[0] if stages else "new"


def seq_active(lead: dict) -> bool:
    seq = lead.get("sequence") or {}
    return bool(seq) and not seq.get("stopped")


def linkedin_active(lead: dict) -> bool:
    seq = lead.get("linkedin_seq") or {}
    return bool(seq) and seq.get("status") not in ("stopped", "done", "finished", "completed") and not seq.get("stopped")


class Index:
    """Per-lead touches, replies and open tasks, built once per request (not once per lead)."""

    def __init__(self, store):
        self.out, self.inn, self.tasks = {}, {}, {}
        add = lambda bucket, lid, at, kind: lid and bucket.setdefault(lid, []).append((at, kind))
        for m in store.read("outbox"):
            add(self.out, m.get("lead_id"), m.get("sent_at"), "email")
        for m in store.read("whatsapp_outbox"):
            add(self.out, m.get("lead_id"), m.get("sent_at"), "whatsapp")
        for m in store.read("inbox"):
            add(self.inn, m.get("lead_id"), m.get("received_at"), m.get("channel", "email"))
        for a in store.read("activities"):
            if a.get("direction", "out") == "out":
                add(self.out, a.get("lead_id"), a.get("at"), a.get("type", "activity"))
            elif a.get("direction") == "in":
                add(self.inn, a.get("lead_id"), a.get("at"), a.get("type", "activity"))
        for r in store.read("settings").get("linkedin_agent_log") or []:
            if r.get("kind") in ("connect", "message"):
                add(self.out, r.get("lead_id"), r.get("at"), "linkedin")
        for t in store.read("tasks"):
            if t.get("lead_id"):
                t = _normalise_task(t)
                if t["status"] == "open":
                    self.tasks.setdefault(t["lead_id"], []).append(t)

    def lead_numbers(self, lead: dict) -> dict:
        lid = lead["id"]
        out = list(self.out.get(lid, []))
        out += [(e.get("at"), "linkedin") for e in lead.get("timeline", []) if e.get("kind") == "linkedin"]
        inn = list(self.inn.get(lid, []))
        timeline_replies = [e.get("at") for e in lead.get("timeline", []) if is_person_reply(e)]
        replies = max(len(inn), len(timeline_replies), int((lead.get("engagement") or {}).get("replies") or 0))
        stamps = [parse_when(at) for at, _ in out + inn] + [parse_when(at) for at in timeline_replies]
        stamps = [s for s in stamps if s]
        tasks = sorted(self.tasks.get(lid, []), key=lambda t: (parse_when(t.get("due_at")) or datetime.max.replace(tzinfo=timezone.utc)))
        nxt = tasks[0] if tasks else None
        return {"touches": len(out), "replies": replies, "last_touch": max(stamps) if stamps else None,
                "out_days": {d for d in (ist_day(at) for at, _ in out) if d},
                "next_task": {"id": nxt["id"], "title": nxt["title"], "due_at": nxt.get("due_at")} if nxt else None,
                "open_tasks": len(tasks)}


def _company_name(rows: list, key: str) -> str:
    names = {}
    for l in rows:
        n = (l.get("company") or "").strip()
        if n:
            names[n] = names.get(n, 0) + 1
    if names:
        return max(names.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    if key.startswith("d:"):
        return key[2:]
    return rows[0].get("contact_name") or rows[0].get("email") or "Unnamed"


def overlap_warnings(rows: list, numbers: dict) -> list:
    """Pairs of contacts both in an active sequence who were touched or enrolled on the same IST day."""
    active = [l for l in rows if seq_active(l) or linkedin_active(l)]
    out = []
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            days_a = set(numbers[a["id"]]["out_days"]) | {d for d in (ist_day((a.get("sequence") or {}).get("enrolled_at")),
                                                                         ist_day((a.get("linkedin_seq") or {}).get("started_at"))) if d}
            days_b = set(numbers[b["id"]]["out_days"]) | {d for d in (ist_day((b.get("sequence") or {}).get("enrolled_at")),
                                                                         ist_day((b.get("linkedin_seq") or {}).get("started_at"))) if d}
            same = sorted(days_a & days_b)
            if same:
                out.append({"lead_ids": [a["id"], b["id"]],
                            "names": [a.get("contact_name") or a.get("email") or a["id"], b.get("contact_name") or b.get("email") or b["id"]],
                            "days": same[-5:],
                            "text": f"{a.get('contact_name') or 'One contact'} and {b.get('contact_name') or 'another contact'} are both in active "
                                    f"sequences and were contacted on the same day ({same[-1]} IST)."})
    return out


def more_people(members: list) -> list:
    """People kept on a lead's other_contacts (a second person merged in on import or enrichment) who are not
    leads themselves: shown next to the leads so nobody at the company is hidden."""
    known_emails = {(l.get("email") or "").strip().lower() for l in members if l.get("email")}
    known_names = {norm_name(l.get("contact_name")) for l in members if l.get("contact_name")}
    out, seen = [], set()
    for l in members:
        for c in l.get("other_contacts") or []:
            if not isinstance(c, dict):
                continue
            email = (c.get("email") or "").strip().lower()
            name = norm_name(c.get("contact_name"))
            key = email or name
            if not key or key in seen or (email and email in known_emails) or (not email and name and name in known_names):
                continue
            seen.add(key)
            out.append({"contact_name": c.get("contact_name") or "", "title": c.get("title") or "", "email": c.get("email") or "",
                        "phone": c.get("phone") or "", "linkedin_url": c.get("linkedin_url") or "",
                        "kept_on": l["id"], "kept_on_name": l.get("contact_name") or l.get("email") or l.get("company") or ""})
    out.sort(key=lambda c: (c["contact_name"] or c["email"]).lower())
    return out


def summarise(store, key: str, rows: list, numbers: dict, health_cache: dict) -> dict:
    stages = [l.get("stage", "new") for l in rows]
    scores = [score_lead({**l, "consent_yes": bool(l.get("consent"))})["score"] for l in rows]
    lasts = [numbers[l["id"]]["last_touch"] for l in rows if numbers[l["id"]]["last_touch"]]
    replies = sum(numbers[l["id"]]["replies"] for l in rows)
    domain = key[2:] if key.startswith("d:") else None
    health = health_cache.get(domain) if domain else None
    owners = sorted({str(l["owner"]) for l in rows if l.get("owner")})
    return {
        "key": key,
        "name": _company_name(rows, key),
        "domain": domain,
        "contacts": len(rows) + len(more_people(rows)),
        "best_stage": best_stage(stages),
        "engaged": replies > 0 and not all(s in EXIT_STAGES for s in stages),
        "touches": sum(numbers[l["id"]]["touches"] for l in rows),
        "last_touch": iso_utc(max(lasts)) if lasts else None,
        "replies": replies,
        "open_roles": max((int(l.get("open_roles") or 0) for l in rows), default=0),
        "score": max(scores) if scores else 0,
        "health": {"score": health.get("score"), "band": health.get("band"), "built_at": health.get("built_at")} if health else None,
        "owners": owners,
        "linkedin_accepted": sum(1 for l in rows if (l.get("linkedin_seq") or {}).get("accepted_at")),
        "active_sequences": sum(1 for l in rows if seq_active(l) or linkedin_active(l)),
        "workspaces": sorted({workspaces.ws_of(l) for l in rows}),
    }


def _groups(store):
    leads = workspaces.filter_rows(store.read("leads"))
    keys = group_keys(leads)
    groups = {}
    for l in leads:
        groups.setdefault(keys[l["id"]], []).append(l)
    return groups


def list_companies(store, q: str = "", sort: str = "last_touch", order: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    groups = _groups(store)
    index = Index(store)
    health_cache = store.read("settings").get("company_health") or {}
    needle = norm_name(q)
    rows = []
    for key, members in groups.items():
        if needle and not any(needle in norm_name(v) for l in members for v in (l.get("company"), l.get("domain"), l.get("contact_name"), l.get("email"))) \
                and needle not in key:
            continue
        numbers = {l["id"]: index.lead_numbers(l) for l in members}
        rows.append(summarise(store, key, members, numbers, health_cache))
    sort = sort if sort in SORTS else "last_touch"
    keyfn = {
        "last_touch": lambda r: parse_when(r["last_touch"]) or _EPOCH,
        "contacts": lambda r: r["contacts"],
        "score": lambda r: r["score"],
        "health": lambda r: (r["health"] or {}).get("score") if r["health"] and r["health"].get("score") is not None else -1,
        "name": lambda r: natural_key(r["name"]),
    }[sort]
    rows.sort(key=lambda r: natural_key(r["name"]))
    rows.sort(key=keyfn, reverse=(order != "asc"))
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    return {"total": len(rows), "limit": limit, "offset": offset, "sort": sort, "order": order, "items": rows[offset:offset + limit]}


def detail(store, key: str) -> Optional[dict]:
    groups = _groups(store)
    members = groups.get(key)
    if not members:
        return None
    index = Index(store)
    numbers = {l["id"]: index.lead_numbers(l) for l in members}
    health_cache = store.read("settings").get("company_health") or {}
    summary = summarise(store, key, members, numbers, health_cache)
    contacts = []
    for l in members:
        n = numbers[l["id"]]
        contacts.append({"id": l["id"], "contact_name": l.get("contact_name", ""), "title": l.get("title", ""), "email": l.get("email"),
                         "phone": l.get("phone"), "stage": l.get("stage", "new"), "score": score_lead({**l, "consent_yes": bool(l.get("consent"))})["score"],
                         "last_touch": iso_utc(n["last_touch"]), "touches": n["touches"], "replies": n["replies"], "next_task": n["next_task"],
                         "in_sequence": seq_active(l), "in_linkedin": linkedin_active(l), "owner": l.get("owner"),
                         "linkedin_accepted": bool((l.get("linkedin_seq") or {}).get("accepted_at"))})
    contacts.sort(key=lambda c: (-stage_rank(c["stage"]), -(parse_when(c["last_touch"]) or _EPOCH).timestamp(), c["contact_name"].lower()))
    timeline = []
    for l in members:
        who = {"id": l["id"], "name": l.get("contact_name") or l.get("email") or l["id"]}
        timeline += [{**item, "contact": who} for item in lead_activity(store, l)]
    timeline.sort(key=lambda i: parse_when(i.get("at")) or _EPOCH, reverse=True)
    domain = summary["domain"]
    return {**summary, "contacts_list": contacts, "more_people": more_people(members), "timeline": timeline[:MAX_TIMELINE], "timeline_total": len(timeline),
            "warnings": overlap_warnings(members, numbers),
            "health_report": company_health.cached(store, domain) if domain else None,
            "open_tasks": sum(n["open_tasks"] for n in numbers.values()),
            "health_lead_id": next((l["id"] for l in members if lead_domain(l)), None)}


def of_lead(store, lead_id: str) -> Optional[dict]:
    """The company a lead belongs to, with the other people there (for the lead drawer)."""
    groups = _groups(store)
    for key, members in groups.items():
        if any(l["id"] == lead_id for l in members):
            others = [{"id": l["id"], "contact_name": l.get("contact_name", ""), "title": l.get("title", ""), "email": l.get("email"),
                       "stage": l.get("stage", "new"), "in_sequence": seq_active(l)} for l in members if l["id"] != lead_id]
            others.sort(key=lambda c: (-stage_rank(c["stage"]), c["contact_name"].lower()))
            return {"key": key, "name": _company_name(members, key), "others": others, "more_people": more_people(members)}
    return None
