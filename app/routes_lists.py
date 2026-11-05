"""Lead lists API: build a list from picked leads / company names / enriched leads, then start outreach for it."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lists


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

    def found(list_id: str) -> dict:
        row = lists.get(store, list_id)
        if row is None:
            raise HTTPException(404, "List not found")
        return row

    @router.get("/api/lists")
    def all_lists():
        return [{**{k: v for k, v in r.items() if k not in ("lead_ids", "log")}, "stats": lists.stats(store, r)} for r in reversed(store.read("lists"))]

    @router.get("/api/lists/companies")
    def companies():
        """Company names with lead counts, for the company picker."""
        seen = {}
        for l in store.read("leads"):
            name = (l.get("company") or "").strip()
            if name:
                row = seen.setdefault(name.lower(), {"company": name, "leads": 0, "with_email": 0, "with_linkedin": 0, "enriched": 0})
                row["leads"] += 1
                row["with_email"] += 1 if l.get("email") else 0
                row["with_linkedin"] += 1 if l.get("linkedin_url") else 0
                row["enriched"] += 1 if lists.is_enriched(l) else 0
        return sorted(seen.values(), key=lambda r: r["company"].lower())

    @router.get("/api/lists/enriched-summary")
    def enriched_summary():
        leads = [l for l in store.read("leads") if lists.is_enriched(l)]
        ready = [l for l in leads if lists.ready_for_email(l)]
        return {"enriched": len(leads), "email_ready": len(ready), "ready_ids": [l["id"] for l in ready][:2000],
                "companies": len({(l.get("company") or l["id"]).lower() for l in ready})}

    @router.post("/api/lists")
    def create(body: ListBody, request: Request):
        ids = lists.resolve(store, body.lead_ids, body.companies, body.enriched)
        if not ids:
            raise HTTPException(400, "No leads match. Pick leads or company names first.")
        how = ", ".join(x for x in ("picked leads" if body.lead_ids else "", f"{len(body.companies)} companies" if body.companies else "",
                                    "enriched leads" if body.enriched else "") if x)
        return lists.create(store, body.name, ids, who(request), how)

    @router.get("/api/lists/{list_id}")
    def one(list_id: str):
        row = found(list_id)
        by_id = {l["id"]: l for l in store.read("leads")}
        members = [{**{k: by_id[i].get(k) for k in ("id", "company", "contact_name", "title", "email", "phone", "linkedin_url", "stage")},
                    "journey": lists.journey(by_id[i])} for i in row["lead_ids"] if i in by_id]
        return {**{k: v for k, v in row.items() if k != "lead_ids"}, "members": members, "stats": lists.stats(store, row)}

    @router.post("/api/lists/{list_id}/members")
    def members(list_id: str, body: MembersBody, request: Request):
        found(list_id)
        return lists.set_members(store, list_id, lists.resolve(store, body.add), body.remove, who(request))

    @router.delete("/api/lists/{list_id}")
    def remove(list_id: str):
        if not lists.delete(store, list_id):
            raise HTTPException(404, "List not found")
        return {"ok": True}

    @router.post("/api/lists/{list_id}/start-email")
    def start_email(list_id: str, request: Request, body: StartBody = StartBody()):
        found(list_id)
        return lists.start_email(store, list_id, who(request), body.lead_ids)

    @router.post("/api/lists/{list_id}/start-linkedin")
    def start_linkedin(list_id: str, request: Request, body: StartBody = StartBody()):
        found(list_id)
        return lists.start_linkedin(store, list_id, who(request), body.lead_ids)

    return router
