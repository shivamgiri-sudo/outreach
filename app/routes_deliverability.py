"""GET /api/deliverability (who bounces: by source, domain, step and sending mailbox) and GET /api/deliverability/dns (the
sending domain's records). Owner and workspace admin only. Responses hold counts, domains and our own sending addresses,
never a lead's address or name.

Owner only (the role guard: every write not listed for callers needs an owner):
  PUT  /api/deliverability/volume           the share of each mailbox's daily sends for unconfirmed addresses (app/send_volume.py)
  POST /api/deliverability/senders/resume   lift one sending mailbox's bounce pause (app/sender_health.py)"""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import accounts, audit, deliverability, lead_access, send_volume, sender_dns, sender_health, workspaces


class VolumeBody(BaseModel):
    domain_ready_share: int = Field(ge=0, le=100)


class ResumeBody(BaseModel):
    sender: str = Field(min_length=1, max_length=254)


def make_router(store, resolver=None) -> APIRouter:
    router = APIRouter()

    def visible_leads(request: Request) -> list:
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
        if not accounts.is_client(user):                 # like the platform's own 7-day pause (inbound._rates): Mas Callnet's sends only,
            amap = accounts.ws_account_map(store)        # a client account's mail is reported to that account's own admins
            leads = [l for l in leads if accounts.account_of_lead(amap, l) == accounts.MCN]
        return leads

    @router.get("/api/deliverability")
    def get_report(request: Request, days: int = Query(30, ge=1, le=deliverability.MAX_DAYS)):
        leads = visible_leads(request)
        ids = {l["id"] for l in leads}
        return {**deliverability.report(store, leads, days),
                "by_sender": sender_health.meter(store, keep=lambda m: m.get("lead_id") in ids),
                "sender_thresholds": sender_health.thresholds(), "volume": send_volume.view(store)}

    @router.put("/api/deliverability/volume")
    def put_volume(body: VolumeBody, request: Request):
        share = send_volume.save_share(store, body.domain_ready_share)
        audit.record(store, getattr(request.state, "user", None), "deliverability.volume", "unconfirmed addresses",
                     f"{share}% of each mailbox's daily sends may go to addresses whose mailbox is not confirmed")
        return send_volume.view(store)

    @router.post("/api/deliverability/senders/resume")
    def resume_sender(body: ResumeBody, request: Request):
        what = sender_health.resume(store, body.sender)
        if not what:
            raise HTTPException(404, "That mailbox is not paused.")
        audit.record(store, getattr(request.state, "user", None), "deliverability.resume_sender", body.sender[:120],
                     "bounce pause lifted" + (" (all sending)" if what == "all" else ""))
        return {"ok": True, "resumed": what}

    @router.get("/api/deliverability/dns")
    def get_dns(request: Request):
        user = getattr(request.state, "user", None)
        return sender_dns.report(store, resolver=resolver, own_account=accounts.is_client(user))

    return router
