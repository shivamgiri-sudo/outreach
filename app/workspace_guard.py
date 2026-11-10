"""Deny by default for logins limited to some workspaces (every client-account login is one: app/accounts.py).

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
    ("GET", "/api/me/two-step"), ("POST", "/api/me/two-step/start"), ("POST", "/api/me/two-step/confirm"), ("POST", "/api/me/two-step/off"),
    ("POST", "/api/me/two-step/recovery"), ("GET", "/api/platform/status"), ("GET", "/api/signup/pending"),   # banners, waiting page
    ("GET", "/api/account/audit"),                # a client admin's own account's change log (the route checks)
    ("GET", "/api/account/usage"), ("GET", "/api/account/whatsapp"), ("PUT", "/api/account/whatsapp"),      # an account's admins: usage card,
    ("DELETE", "/api/account/whatsapp"), ("PUT", "/api/account/alerts"),                                     # own WhatsApp, alert settings
    # app shell: labels, brand, help, read-only content and config the pages need (no lead data)
    ("GET", "/api/brand/public"), ("GET", "/api/brand"), ("GET", "/api/brand/logo"), ("GET", "/api/labels"), ("GET", "/api/guides"),
    ("GET", "/api/settings"), ("GET", "/api/settings/live-sending"), ("GET", "/api/sending/status"), ("GET", "/api/integrations"),
    ("GET", "/api/channels"), ("GET", "/api/lead-fields"), ("GET", "/api/lead-sources"), ("GET", "/api/team"), ("GET", "/api/offer"),
    ("GET", "/api/sequence"), ("GET", "/api/sequence/{index}/preview"), ("GET", "/api/sequences"), ("GET", "/api/sequences/{seq_id}"),
    ("GET", "/api/sequences/{seq_id}/steps/{index}/preview"), ("POST", "/api/sequences/preview-draft"), ("GET", "/api/snippets"),
    ("GET", "/api/presets"), ("GET", "/api/presets/{preset_id}"), ("GET", "/api/whatsapp/status"), ("GET", "/api/whatsapp/templates"),
    ("GET", "/api/icp"), ("GET", "/api/call-script"), ("POST", "/api/prompts/generate"), ("POST", "/api/activation/event"),
    ("GET", "/api/accounts"),                     # a client login sees only its own account (the route checks)
    ("POST", "/api/workspaces"),                  # a non-owner's new workspace is only a request until an owner approves it
    ("GET", "/api/workspaces"), ("GET", "/api/workspaces/overview"), ("PUT", "/api/workspaces/{ws_id}"),       # rename/members: the routes
    ("GET", "/api/workspaces/{ws_id}/members"), ("POST", "/api/workspaces/{ws_id}/members"),            # check owner / own-workspace admin
    ("DELETE", "/api/workspaces/{ws_id}/members/{email}"),
    ("GET", "/api/lead-views"), ("POST", "/api/lead-views"), ("DELETE", "/api/lead-views/{view_id}"),
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
    ("GET", "/api/outbox/internal"), ("GET", "/api/outbox/export.csv"),      # owner/admin only; rows filtered by lead scope
    ("GET", "/api/reply-inbox"), ("POST", "/api/reply-inbox/action"),
    ("GET", "/api/meetings"), ("POST", "/api/meetings/{meeting_id}/cancel"), ("POST", "/api/meetings/{meeting_id}/done"),
    ("GET", "/api/quality"), ("GET", "/api/change-log"), ("GET", "/api/whatsapp/outbox"), ("GET", "/api/readiness"),
    # browser extension: pairing its own token, saving a profile (into this workspace), matching and its queue
    ("POST", "/api/extension/pair"), ("GET", "/api/extension/tokens"), ("DELETE", "/api/extension/tokens/{token_id}"),
    ("POST", "/api/extension/save-profile"), ("GET", "/api/extension/match"), ("GET", "/api/extension/queue"),
    # LinkedIn account per lead and the stage board (both only touch / count leads in the login's workspaces)
    ("GET", "/api/linkedin/accounts"), ("POST", "/api/linkedin/assign"), ("GET", "/api/linkedin/stages"), ("GET", "/api/linkedin/stages.csv"),
    # the account's own LinkedIn agent, cloud slots and office computer (owners and the account's admins change them; the
    # routes cut every view and change to the login's account: app/linkedin_scope.py)
    ("GET", "/api/linkedin-agent"), ("PUT", "/api/linkedin-agent"), ("GET", "/api/linkedin-cloud"), ("POST", "/api/linkedin-cloud/slots"),
    ("PUT", "/api/linkedin-cloud/slots/{slot_id}"), ("DELETE", "/api/linkedin-cloud/slots/{slot_id}"),
    ("POST", "/api/linkedin-cloud/slots/{slot_id}/login"), ("POST", "/api/linkedin-cloud/slots/{slot_id}/code"),
    ("POST", "/api/linkedin-cloud/slots/{slot_id}/logout"), ("POST", "/api/linkedin-cloud/slots/{slot_id}/restart"),
    ("POST", "/api/linkedin-cloud/runner/pair"), ("DELETE", "/api/linkedin-cloud/runner/pair"),
    ("PUT", "/api/linkedin-cloud/runner/{runner_id}"), ("DELETE", "/api/linkedin-cloud/runner/{runner_id}"),
    # templates of the login's workspaces (+ main's, read-only). Writes: owners, and a workspace's own admins only; the
    # routes check the template's workspace (app/ws_templates.py) and refuse callers.
    ("POST", "/api/sequences"), ("PUT", "/api/sequences/{seq_id}"), ("POST", "/api/sequences/{seq_id}/duplicate"),
    ("POST", "/api/sequences/{seq_id}/archive"), ("POST", "/api/sequences/{seq_id}/make-default"), ("GET", "/api/sequences/{seq_id}/stats"),
    ("POST", "/api/snippets"), ("PUT", "/api/snippets/{snippet_id}"), ("DELETE", "/api/snippets/{snippet_id}"),
    # the LinkedIn page: queue and suggestions of the login's leads only, its workspace's steps / notes
    ("GET", "/api/linkedin/queue"), ("GET", "/api/linkedin/steps"), ("GET", "/api/linkedin/sequence"), ("PUT", "/api/linkedin/sequence"),
    ("POST", "/api/linkedin/sequence/reset"), ("POST", "/api/linkedin/sequence/copy"), ("POST", "/api/linkedin/sequence/preview"),
    ("GET", "/api/linkedin/variants"), ("PUT", "/api/linkedin/variants"), ("POST", "/api/linkedin/variants/score"),
    # flows of the login's workspaces (+ main's, read-only); a workspace's flow only takes that workspace's leads
    ("GET", "/api/flows"), ("GET", "/api/flows/catalog"), ("GET", "/api/flows/templates"), ("POST", "/api/flows"),
    ("POST", "/api/flows/import"), ("POST", "/api/flows/templates/{key}"), ("GET", "/api/flows/{flow_id}"), ("PUT", "/api/flows/{flow_id}"),
    ("DELETE", "/api/flows/{flow_id}"), ("GET", "/api/flows/{flow_id}/export"), ("POST", "/api/flows/{flow_id}/validate"),
    ("POST", "/api/flows/{flow_id}/activate"), ("POST", "/api/flows/{flow_id}/pause"), ("POST", "/api/flows/{flow_id}/enroll"),
    ("POST", "/api/flows/{flow_id}/simulate"), ("GET", "/api/flows/{flow_id}/runs"),
    # workspace admins: logins of their own workspaces (the routes check every target and workspace)
    ("GET", "/api/users"), ("POST", "/api/users"), ("PUT", "/api/users/{email}"), ("DELETE", "/api/users/{email}"),
    ("GET", "/api/security/logins"),          # login history: the route shows an admin only its own workspaces' logins
    ("GET", "/api/funnel"), ("GET", "/api/funnel/drill"), ("GET", "/api/funnel/settings"),      # sales funnel: leads in scope only
    ("PUT", "/api/funnel/settings"), ("POST", "/api/funnel/recalculate"),                      # owners / the workspace's admins (routes_auth)
    ("GET", "/api/reports/sla"), ("GET", "/api/reports/daily"), ("GET", "/api/reports/daily.csv"), ("POST", "/api/reports/email"),
    ("POST", "/api/import/preview"), ("POST", "/api/import/excel"), ("POST", "/api/import/commit"), ("GET", "/api/import/history"), ("POST", "/api/import/{import_id}/undo"),
}
# The LinkedIn agent program's calls, for a pairing / slot token of a client account only (never a web login): its own
# account's leads only (the token's workspaces; app/linkedin_scope.py).
AGENT_TOKEN_OK = {("GET", "/api/extension/agent"), ("GET", "/api/extension/lookup/next"), ("POST", "/api/extension/agent/event"), ("POST", "/api/extension/inbox"),
                  ("GET", "/api/extension/cloud/login-job"), ("GET", "/api/extension/cloud/code"), ("POST", "/api/extension/cloud/state")}
# Client logins (another company's account, app/accounts.py) are also refused these: copying Mas Callnet's own
# LinkedIn steps into their workspace.
CLIENT_DENY = {("POST", "/api/linkedin/sequence/copy")}
# (offer, call script, ICP, prompt writer and sequence preview answer a client with its own account's values instead:
# accounts.view_for in those routes.)
LEAD_PARAMS = ("{lead_id}", "{task_id}")
# A login that lost its last workspace sees nothing and may change nothing but its own account.
NO_WORKSPACE_WRITES = {("POST", "/api/logout"), ("POST", "/api/me/password"), ("POST", "/api/me/two-step/start"),
                       ("POST", "/api/me/two-step/confirm"), ("POST", "/api/me/two-step/off"), ("POST", "/api/me/two-step/recovery")}
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
    if allowed_of(user) == set() and request.method not in ("GET", "HEAD", "OPTIONS") and (request.method, path) not in NO_WORKSPACE_WRITES:
        raise HTTPException(403, "Your login has no workspace yet. Ask the owner to give you one.")
    agent_call = user.get("kind") == "extension" and (request.method, path) in AGENT_TOKEN_OK
    if path.startswith("/api") and not allowed_route(request.method, path) and not agent_call:
        raise HTTPException(403, "This page is not available for logins limited to a workspace. Ask the owner.")
    if (user.get("account_id") or "mcn") != "mcn" and (request.method, path) in CLIENT_DENY:
        raise HTTPException(403, "This page belongs to the platform account. Ask the platform admin.")
