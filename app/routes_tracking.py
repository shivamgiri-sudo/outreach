"""Public open-pixel and click-redirect endpoints for outreach emails (see tracking.py). No login: the signed token is
the key, and a bad token still gets the image / a safe page, so nothing can be learned by probing."""
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, Response

from . import tracking

PUBLIC_PREFIX = "/api/track/"
NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def make_router(store, base_url_of) -> APIRouter:
    router = APIRouter()

    @router.get("/api/track/o/{token}.gif")
    def opened(token: str):
        key = tracking.read_token(store, token[:400])
        if key:
            try:
                tracking.record(store, key, "open")
            except Exception:                      # the image is always served
                pass
        return Response(tracking.PIXEL, media_type="image/gif", headers=NO_CACHE)

    @router.get("/api/track/c/{token}")
    def clicked(token: str, u: str = Query("", max_length=2000), s: str = Query("", max_length=64)):
        if not tracking.link_ok(store, u, s):
            return RedirectResponse(base_url_of() or "/", status_code=302)
        key = tracking.read_token(store, token[:400])
        if key:
            try:
                tracking.record(store, key, "click", u)
            except Exception:
                pass
        return RedirectResponse(u, status_code=302, headers=NO_CACHE)

    return router
