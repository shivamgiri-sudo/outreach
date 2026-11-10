"""Lead lists API: build a list from picked leads / company names / enriched leads, then start outreach for it."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lead_access, list_assign, lists, workspaces as ws


class ListBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lead_ids: List[str] = []
    companies: List[str] = []
    enriched: bool = False


class MembersBody(BaseModel):
    add: List[str] = []
    remove: List[str] = []


class StartBody(BaseModel):
    lead_ids: Optional[List[str]] = None


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    def who(request: Request) -> str:
        return audit.actor_of(getattr(request.state, "user", None))["name"]

    def user_of(request: Request):
        return getattr(request.state, "user", None)

    def found(list_id: str, request: Request, work: bool = False) -> dict:
        """The list, or 404. With lead ownership on a member sees only lists assigned to / shared with them; `work`
        (start outreach, change members, delete) needs to be the assignee or an admin, a view-only share is refused."""
        row = lists.get(store, list_id)
        if row is None or not ws.visible(row) or not list_assign.visible(store, user_of(request), row):
            raise HTTPException(404, "List not found")
        if work and not list_assign.may_work(store, user_of(request), row):
            raise HTTPException(409, "This list is shared with you to look at only. Ask an admin to assign it to you.")
        return row

    def seen_leads(request: Request) -> list:
        return lead_access.visible_leads(store, user_of(request), ws.filter_rows(store.read("leads")))

    def refuse_if_all_owner_blocked(result: dict) -> dict:
        if result.get("asked") and result.get("owner_refused") == result["asked"]:
            raise HTTPException(409, next(r["reason"] for r in result["refused"]))
        return result

    @router.get("/api/lists")
    def all_lists(request: Request):
        rows = [r for r in ws.filter_rows(store.read("lists")) if list_assign.visible(store, user_of(request), r)]
        return [{**{k: v for k, v in r.items() if k not in ("lead_ids", "log")}, "stats": lists.stats(store, r)} for r in reversed(rows)]

    @router.get("/api/lists/companies")
    def companies(request: Request):
        """Company names with lead counts, for the company picker."""
        seen = {}
        for l in seen_leads(request):
            name = (l.get("company") or "").strip()
            if name:
                row = seen.setdefault(name.lower(), {"company": name, "leads": 0, "with_email": 0, "with_linkedin": 0, "enriched": 0})
                row["leads"] += 1
                row["with_email"] += 1 if l.get("email") else 0
                row["with_linkedin"] += 1 if l.get("linkedin_url") else 0
                row["enriched"] += 1 if lists.is_enriched(l) else 0
        return sorted(seen.values(), key=lambda r: r["company"].lower())

    @router.get("/api/lists/enriched-summary")
    def enriched_summary(request: Request):
        leads = [l for l in seen_leads(request) if lists.is_enriched(l)]
        ready = [l for l in leads if lists.ready_for_email(l)]
        return {"enriched": len(leads), "email_ready": len(ready), "ready_ids": [l["id"] for l in ready][:2000],
                "companies": len({(l.get("company") or l["id"]).lower() for l in ready})}

    @router.post("/api/lists")
    def create(body: ListBody, request: Request):
        ids = lists.resolve(store, body.lead_ids, body.companies, body.enriched, user_of(request))
        if not ids:
            raise HTTPException(400, "No leads match. Pick leads or company names first.")
        how = ", ".join(x for x in ("picked leads" if body.lead_ids else "", f"{len(body.companies)} companies" if body.companies else "",
                                    "enriched leads" if body.enriched else "") if x)
        mine = "" if lead_access.is_admin(user_of(request), ws.for_new_lead()) or user_of(request) is None else who(request)   # a member's own list is theirs
        return lists.create(store, body.name, ids, who(request), how, assigned_to=mine)

    @router.get("/api/lists/{list_id}")
    def one(list_id: str, request: Request):
        row = found(list_id, request)
        by_id = {l["id"]: l for l in seen_leads(request)}
        members = [{**{k: by_id[i].get(k) for k in ("id", "company", "contact_name", "title", "email", "phone", "linkedin_url", "stage")},
                    "journey": lists.journey(by_id[i])} for i in row["lead_ids"] if i in by_id]
        return {**{k: v for k, v in row.items() if k != "lead_ids"}, "members": members, "stats": lists.stats(store, row)}

    @router.post("/api/lists/{list_id}/members")
    def members(list_id: str, body: MembersBody, request: Request):
        found(list_id, request, work=True)
        return lists.set_members(store, list_id, lists.resolve(store, body.add, user=user_of(request)), body.remove, who(request))

    @router.delete("/api/lists/{list_id}")
    def remove(list_id: str, request: Request):
        row = found(list_id, request, work=True)
        if not lists.delete(store, list_id):
            raise HTTPException(404, "List not found")
        audit.record(store, getattr(request.state, "user", None), "list.delete", (row or {}).get("name") or list_id,
                     f"{len((row or {}).get('lead_ids') or [])} leads (the leads stay)")
        return {"ok": True}

    @router.post("/api/lists/{list_id}/start-email")
    def start_email(list_id: str, request: Request, body: StartBody = StartBody()):
        row = found(list_id, request, work=True)
        result = refuse_if_all_owner_blocked(lists.start_email(store, list_id, who(request), body.lead_ids, user_of(request)))
        audit.record(store, getattr(request.state, "user", None), "bulk.list_email", row["name"], f"started {result['started']} of {result['asked']}")
        return result

    @router.post("/api/lists/{list_id}/start-linkedin")
    def start_linkedin(list_id: str, request: Request, body: StartBody = StartBody()):
        row = found(list_id, request, work=True)
        result = refuse_if_all_owner_blocked(lists.start_linkedin(store, list_id, who(request), body.lead_ids, user_of(request)))
        audit.record(store, getattr(request.state, "user", None), "bulk.list_linkedin", row["name"],
                     f"started {result.get('started', 0)} of {result.get('asked', 0)}, {result.get('looking_up', 0)} being looked up")
        return result

    return router
