"""GET /api/deliverability (who bounces: by source, domain and step) and GET /api/deliverability/dns (the sending domain's records).
Owner and workspace admin only. Responses hold counts and domains, never addresses or names."""
from fastapi import APIRouter, Query, Request

from . import accounts, deliverability, lead_access, sender_dns, workspaces


def make_router(store, resolver=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/deliverability")
    def get_report(request: Request, days: int = Query(30, ge=1, le=deliverability.MAX_DAYS)):
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
        if not accounts.is_client(user):                 # like the platform's own 7-day pause (inbound._rates): Mas Callnet's sends only,
            amap = accounts.ws_account_map(store)        # a client account's mail is reported to that account's own admins
            leads = [l for l in leads if accounts.account_of_lead(amap, l) == accounts.MCN]
        return deliverability.report(store, leads, days)

    @router.get("/api/deliverability/dns")
    def get_dns(request: Request):
        user = getattr(request.state, "user", None)
        return sender_dns.report(store, resolver=resolver, own_account=accounts.is_client(user))

    return router
