"""Deny by default for logins limited to some workspaces.

A limited login (caller or viewer with workspaces ticked) may only call the endpoints listed here, each checked to
show or change nothing outside its workspaces. Any other endpoint, including every endpoint added later, answers
403 until someone checks it and adds it. Per-lead routes (/{lead_id}, /{task_id}) are allowed because the lead scope
guard in main.py already answers 404 for a lead outside the login's workspaces.
"""
from typing import Optional

from fastapi import HTTPException, Request

from .workspaces import allowed_of

LIMITED_OK = {
    # signing in and the login's own account
    ("GET", "/api/auth/status"), ("POST", "/api/login"), ("POST", "/api/logout"), ("GET", "/api/me"), ("POST", "/api/me/password"),
    # app shell: labels, brand, help, read-only content and config the pages need (no lead data)
    ("GET", "/api/brand/public"), ("GET", "/api/brand"), ("GET", "/api/brand/logo"), ("GET", "/api/labels"), ("GET", "/api/guides"),
    ("GET", "/api/settings"), ("GET", "/api/settings/live-sending"), ("GET", "/api/sending/status"), ("GET", "/api/integrations"),
    ("GET", "/api/channels"), ("GET", "/api/lead-fields"), ("GET", "/api/lead-sources"), ("GET", "/api/team"), ("GET", "/api/offer"),
    ("GET", "/api/sequence"), ("GET", "/api/sequence/{index}/preview"), ("GET", "/api/sequences"), ("GET", "/api/sequences/{seq_id}"),
    ("GET", "/api/sequences/{seq_id}/steps/{index}/preview"), ("POST", "/api/sequences/preview-draft"), ("GET", "/api/snippets"),
    ("GET", "/api/presets"), ("GET", "/api/presets/{preset_id}"), ("GET", "/api/whatsapp/status"), ("GET", "/api/whatsapp/templates"),
    ("GET", "/api/icp"), ("GET", "/api/call-script"), ("POST", "/api/prompts/generate"), ("POST", "/api/activation/event"),
    ("GET", "/api/workspaces"), ("GET", "/api/lead-views"), ("POST", "/api/lead-views"), ("DELETE", "/api/lead-views/{view_id}"),
    # leads and pages built only from leads in scope
    ("GET", "/api/dashboard"), ("GET", "/api/leads"), ("POST", "/api/leads/import"), ("POST", "/api/leads/bulk"),
    ("GET", "/api/lead-owners"), ("GET", "/api/lead-tags"), ("GET", "/api/companies"), ("GET", "/api/companies/{key:path}"),
    ("GET", "/api/lists"), ("GET", "/api/lists/companies"), ("GET", "/api/lists/enriched-summary"), ("POST", "/api/lists"),
    ("GET", "/api/lists/{list_id}"), ("POST", "/api/lists/{list_id}/members"), ("DELETE", "/api/lists/{list_id}"),
    ("GET", "/api/today"), ("GET", "/api/my-day"), ("PUT", "/api/my-day/targets"), ("GET", "/api/call-queue"),
    ("GET", "/api/tasks"), ("GET", "/api/tasks/board"), ("POST", "/api/tasks"), ("POST", "/api/tasks/done"),
    ("GET", "/api/notifications"), ("POST", "/api/notifications/read"),
    ("GET", "/api/intelligence"), ("GET", "/api/analytics"), ("GET", "/api/analytics/overview"), ("GET", "/api/analytics/pipeline"),
    ("GET", "/api/analytics/pipeline/leads"), ("GET", "/api/insights"),
    ("GET", "/api/outbox"), ("GET", "/api/outbox/message"), ("GET", "/api/inbox"),
    ("GET", "/api/reply-inbox"), ("POST", "/api/reply-inbox/action"),
    ("GET", "/api/meetings"), ("POST", "/api/meetings/{meeting_id}/cancel"), ("POST", "/api/meetings/{meeting_id}/done"),
    ("GET", "/api/quality"), ("GET", "/api/change-log"), ("GET", "/api/whatsapp/outbox"), ("GET", "/api/readiness"),
    # browser extension: pairing its own token, saving a profile (into this workspace), matching and its queue
    ("POST", "/api/extension/pair"), ("GET", "/api/extension/tokens"), ("DELETE", "/api/extension/tokens/{token_id}"),
    ("POST", "/api/extension/save-profile"), ("GET", "/api/extension/match"), ("GET", "/api/extension/queue"),
    ("POST", "/api/import/preview"), ("POST", "/api/import/commit"), ("GET", "/api/import/history"), ("POST", "/api/import/{import_id}/undo"),
}
LEAD_PARAMS = ("{lead_id}", "{task_id}")
NOT_FOR_LEAD_PARAMS = ()


def limited(user: Optional[dict]) -> bool:
    return user is not None and allowed_of(user) is not None


def allowed_route(method: str, path: str) -> bool:
    if (method, path) in LIMITED_OK:
        return True
    return any(p in path for p in LEAD_PARAMS) and not path.startswith(NOT_FOR_LEAD_PARAMS)


def guard(request: Request) -> None:
    """Global dependency, after the role check."""
    user = getattr(request.state, "user", None)
    if not limited(user) or request.method in ("HEAD", "OPTIONS"):
        return
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    if path.startswith("/api") and not allowed_route(request.method, path):
        raise HTTPException(403, "This page is not available for logins limited to a workspace. Ask the owner.")
