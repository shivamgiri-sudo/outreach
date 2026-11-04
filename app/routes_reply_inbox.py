"""GET /api/reply-inbox: every reply from every channel in one list, scored."""
from fastapi import APIRouter

from .reply_inbox import LABELS_SHOWN, build_inbox


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/reply-inbox")
    def inbox(label: str = "", channel: str = "", only_action: bool = False, limit: int = 200):
        data = build_inbox(store.read("leads"), label=label if label in LABELS_SHOWN else "", channel=channel[:20],
                           only_action=only_action, limit=limit)
        data["labels"] = list(LABELS_SHOWN)
        return data

    return router
