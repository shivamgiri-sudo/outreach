"""Chrome assistant API: pairing tokens (web app, signed-in people) and the few routes the extension itself may call."""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import extension_api as ext, extension_save, linkedin_agent as agent_mod, linkedin_seq as li


class PairBody(BaseModel):
    label: str = Field(default="", max_length=60)


class SentBody(BaseModel):
    step: int = Field(ge=1, le=7)


class ProfileBody(BaseModel):
    open_profile: bool = False
    degree: str = Field(default="", max_length=4)


class ReplyBody(BaseModel):
    text: str = Field(default="", max_length=2000)


class InboxItem(BaseModel):
    name: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=2000)
    from_them: bool = False


class InboxBody(BaseModel):
    items: list[InboxItem] = Field(default=[], max_length=60)


class SaveProfileBody(BaseModel):
    profile_url: str = Field(max_length=2000)
    name: str = Field(max_length=extension_save.NAME_MAX)
    title: str = Field(default="", max_length=extension_save.FIELD_MAX)
    company: str = Field(default="", max_length=extension_save.FIELD_MAX)
    location: str = Field(default="", max_length=extension_save.FIELD_MAX)


def make_router(store) -> APIRouter:
    router = APIRouter()
    save_limit = extension_save.HourlyLimit()

    def person(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        user = getattr(request.state, "user", None) or LOCAL_OWNER
        if user.get("kind") == "extension":
            raise HTTPException(403, "A pairing token cannot manage pairing tokens.")
        return user

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return lead

    # ----- pairing (web app) -----
    @router.post("/api/extension/pair")
    def pair(body: PairBody, request: Request):
        try:
            raw, row = ext.create_token(store, person(request), body.label)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {**row, "token": raw, "note": "Copy this now. It is shown only once."}

    @router.get("/api/extension/tokens")
    def list_tokens(request: Request):
        user = person(request)
        return [ext.public_token(t) for t in ext.tokens(store) if (user.get("role") == "owner" or t.get("sub") == user.get("sub"))
                and not str(t.get("label") or "").startswith("cloud:")]      # cloud-agent tokens are managed with their cloud account

    @router.delete("/api/extension/tokens/{token_id}")
    def revoke(token_id: str, request: Request):
        if any(t.get("id") == token_id and str(t.get("label") or "").startswith("cloud:") for t in ext.tokens(store)):
            raise HTTPException(400, "This token belongs to a cloud LinkedIn account. Remove the cloud account instead.")
        if not ext.revoke_token(store, token_id, person(request)):
            raise HTTPException(404, "No such pairing token.")
        return {"ok": True}

    # ----- the extension's own routes -----
    def account_of(request: Request) -> str:
        return agent_mod.norm_account(request.headers.get("x-linkedin-account", ""))

    @router.get("/api/extension/match")
    def match(request: Request, profile_url: str = Query("", max_length=2000), name: str = Query("", max_length=200), company: str = Query("", max_length=200)):
        from . import workspaces
        lead = ext.find_lead(store, profile_url, name, company)
        return ext.describe(store, lead, account_of(request)) if lead and workspaces.visible(lead) else ext.no_match()

    @router.get("/api/extension/queue")
    def queue(request: Request):
        account = account_of(request)
        ctl = agent_mod.control(store, account=account)
        if ctl["active_now"]:                       # daily auto-start: only while the agent is really working
            started = li.auto_start(store, ctl["connections_left_today"])
            if started:
                names = ", ".join(s.get("name") or s.get("company") or "?" for s in started[:8]) + (" …" if len(started) > 8 else "")
                agent_mod.note(store, "auto_start", f"Started the LinkedIn sequence for {len(started)} people: {names}", account)
        return ext.queue(store, account)

    @router.post("/api/extension/inbox")
    def inbox(body: InboxBody, request: Request):
        """What the agent read in the LinkedIn messaging list: replies from people on the sequence stop it and reach Replies."""
        account = account_of(request)
        found = li.match_inbox(store, account, [i.model_dump() for i in body.items])
        for r in found:
            agent_mod.note(store, "replied", f"{r['name']} replied on LinkedIn: {r['text'][:120]}", account, r["lead_id"])
        return {"replied": found}

    @router.post("/api/extension/leads/{lead_id}/claim")
    def claim(lead_id: str, request: Request):
        """Before an agent touches a person: the lead becomes this account's (atomically), unless another account has it."""
        account = account_of(request)
        lead_or_404(lead_id)
        if not account:
            return {"ok": True, "account": ""}
        won = {}

        def apply(l):
            seq = l.get("linkedin_seq")
            if not seq:
                return None
            if seq.get("account") and seq["account"] != account:
                won["other"] = seq["account"]
                return None
            if seq.get("account") == account:
                return None
            return {**l, "linkedin_seq": {**seq, "account": account}, "timeline": [*l["timeline"], {
                "at": li.iso(li.now_utc()), "kind": "linkedin", "text": f"LinkedIn account {account} took this person"}]}
        store.update_lead(lead_id, apply)
        if won.get("other"):
            raise HTTPException(409, f"Another LinkedIn account ({won['other']}) is handling this person.")
        return {"ok": True, "account": account}

    def refuse(info: dict, why: str = ""):
        raise HTTPException(409, why or info["reason"] or "That is not possible right now.")

    @router.post("/api/extension/leads/{lead_id}/sent")
    def sent(lead_id: str, body: SentBody, request: Request):
        info = ext.describe(store, lead_or_404(lead_id), account_of(request))
        if not info["can_send"]:
            refuse(info)
        if info["message"]["step"] != body.step:
            refuse(info, f"The step to send now is {info['message']['step']}, not {body.step}.")
        li.mark_sent(store, lead_id, body.step, origin=_agent_origin(store, lead_or_404(lead_id), body.step, account_of(request), request))
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id))}

    @router.post("/api/extension/leads/{lead_id}/accepted")
    def accepted(lead_id: str, request: Request):
        lead = lead_or_404(lead_id)
        account = account_of(request)
        if account and ext.owner_of(lead) not in ("", account):
            raise HTTPException(409, f"Another LinkedIn account ({ext.owner_of(lead)}) sent this request.")
        if (lead.get("linkedin_seq") or {}).get("status") != "waiting_for_acceptance":
            refuse(ext.describe(store, lead), "This person is not waiting to accept a connection request.")
        result = li.mark_accepted(store, lead_id)
        task = result.get("next_task")
        when = (f"The first message is scheduled for {task['due_at'][:10]}." if task else "No message step is left.")
        from . import alerts
        alerts.safe(store, "li_accepted", lead_id, f"{lead.get('contact_name') or 'They'} accepted your LinkedIn connection request. {when}")
        if account and not ext.owner_of(lead):
            store.update_lead(lead_id, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": account}})
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id), account)}

    @router.post("/api/extension/leads/{lead_id}/profile")
    def profile(lead_id: str, body: ProfileBody):
        """What the agent saw on the profile: the connection degree and whether it is an Open Profile (message without connecting)."""
        lead_or_404(lead_id)
        degree = body.degree if body.degree in ("1st", "2nd", "3rd") else ""
        stamp = li.iso(li.now_utc())

        def apply(l):
            before = (l.get("linkedin_profile") or {}).get("open_profile")
            info = {"open_profile": body.open_profile, "degree": degree, "checked_at": stamp}
            timeline = l["timeline"]
            if body.open_profile and not before:
                timeline = [*timeline, {"at": stamp, "kind": "linkedin", "text": "Open Profile on LinkedIn: can be messaged without connecting"}]
            return {**l, "linkedin_profile": info, "timeline": timeline}
        store.update_lead(lead_id, apply)
        return {"ok": True}

    @router.post("/api/extension/leads/{lead_id}/replied")
    def replied(lead_id: str, body: ReplyBody):
        lead = lead_or_404(lead_id)
        if not lead.get("linkedin_seq"):
            refuse(ext.describe(store, lead), "This person is not on the LinkedIn sequence.")
        li.mark_replied(store, lead_id, body.text.strip())
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id))}

    @router.post("/api/extension/save-profile")
    def save_profile(body: SaveProfileBody, request: Request):
        """The rep pressed "Save to portal" on the one profile they have open: create the lead, or fill its empty fields."""
        user = getattr(request.state, "user", None) or {}
        if user.get("kind") != "extension":
            raise HTTPException(403, "Only the Chrome assistant saves profiles. Add the lead on the Leads page instead.")
        if not save_limit.allow(user.get("token_id") or user.get("sub") or "", extension_save.SAVES_PER_HOUR):
            raise HTTPException(429, f"You have saved {extension_save.SAVES_PER_HOUR} profiles in the last hour. "
                                     "Saving is kept to a human pace; try again later.")
        try:
            return extension_save.save_profile(store, user, body.model_dump(), account_of(request))
        except extension_save.SaveRefused as exc:
            raise HTTPException(exc.status, exc.message)

    return router


def _agent_origin(store, lead: dict, step: int, account: str, request: Request) -> dict:
    """The LinkedIn agent sent this step from a connected account: automatic, with the account and pairing token."""
    from . import provenance
    token = getattr(request.state, "user", None) or {}
    rule = {"account": account or token.get("name") or "the paired browser", "step": f"LinkedIn step {step}",
            "paired_by": token.get("email") or ""}
    return provenance.build(store, "linkedin_agent", True, lead, provenance.system(account=account), rule,
                            provenance.linkedin_enrolment(lead), "linkedin")
