"""Meetings on leads: book through the calendar provider, list, cancel, and attach bookings from webhooks.

lead.meetings = [{id, provider, title, start, end, join_url, status: scheduled|cancelled|done, created_at,
                  external_id, attendee_email, invite?}]. Every change goes through store.update on the latest copy
of the lead, so a reply saved meantime is never overwritten. Bookings never change consent.
Providers that don't email the attendee (Zoom, custom API, a booking link used with a fixed time), or any provider
when "Send our own invite" is ticked, get an .ics invite from us (app/channels/invites.py); cancelling sends the
matching cancellation.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

from ..hygiene import normalise_email
from .calendar import LINK_PROVIDERS, booking_link, cancel_event, create_event
from .config import active_provider, config_for, entry_of
from .http import request
from .invites import needs_our_invite, send_invite

IST = timezone(timedelta(hours=5, minutes=30))
MIN_MINUTES, MAX_MINUTES = 5, 480
REMOTE_CANCEL = ("google", "microsoft", "zoom", "calcom", "calendly")
LEGAL_SUFFIX = re.compile(r"\s+(pvt|private|ltd|limited|llp|inc)\.?(?=\s|$)", re.I)


class NoCalendar(ValueError):
    """No calendar provider is connected (the UI shows a friendly empty state)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(text) -> datetime:
    """Aware datetime from ISO text; a time without a zone is read as IST."""
    value = datetime.fromisoformat(str(text or "").strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=IST)


def ist_label(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%a %d %b %Y, %I:%M %p IST")


def _event(kind: str, text: str) -> dict:
    return {"at": now_utc().isoformat(timespec="seconds"), "kind": kind, "text": text}


def _update_lead(store, lead_id: str, change) -> dict:
    found = {}

    def apply(leads: list) -> list:
        out = []
        for lead in leads:
            if lead["id"] == lead_id:
                lead = change(lead)
                found["lead"] = lead
            out.append(lead)
        return out
    store.update("leads", apply)
    return found.get("lead")


def find_lead(store, lead_id: str) -> dict:
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    return lead


def default_title(lead: dict) -> str:
    company = re.sub(r"\s*\(.*?\)", "", lead.get("company") or lead.get("domain") or "").strip()
    company = LEGAL_SUFFIX.sub("", company).strip()
    return f"BookMyInterview.ai demo — {company}" if company else "BookMyInterview.ai demo"


def book(store, lead_id: str, body: dict, transport=request, resolver=None, now: datetime = None, sender=None) -> dict:
    """Create the meeting (event providers) or a booking link (link providers). Raises NoCalendar / ValueError.

    A plain booking link with a fixed `start` becomes a meeting whose join link is that page, with our invite."""
    now = now or now_utc()
    lead = find_lead(store, lead_id)
    if lead.get("stage") == "unsubscribed":
        raise ValueError("This lead unsubscribed, so no invites can be sent to them.")
    pid = body.get("provider") or active_provider("calendar", entry_of(store.read("integrations"), "calendar"))
    if not pid:
        raise NoCalendar("Connect a calendar first: Integrations → Calendar & meetings.")
    if not config_for(store, "calendar", pid):
        raise NoCalendar("That calendar is not set up yet: Integrations → Calendar & meetings.")
    typed = (body.get("attendee_email") or "").strip()
    attendee = normalise_email(typed or lead.get("email"))
    if typed and not attendee:
        raise ValueError("The attendee email is not valid.")
    fixed_link = pid == "booking_link" and bool(body.get("start"))
    if pid in LINK_PROVIDERS and not fixed_link:
        url = booking_link(store, pid, lead.get("contact_name", ""), attendee or "")
        _update_lead(store, lead_id, lambda l: {**l, "timeline": [*l["timeline"], _event("meeting", f"Booking link prepared: {url}")]})
        return {"kind": "link", "provider": pid, "url": url}
    try:
        start = parse_time(body.get("start"))
    except ValueError:
        raise ValueError("Pick a date and time for the meeting.") from None
    duration = int(body.get("duration_minutes") or 30)
    if not MIN_MINUTES <= duration <= MAX_MINUTES:
        raise ValueError(f"Duration must be between {MIN_MINUTES} and {MAX_MINUTES} minutes.")
    if start < now - timedelta(minutes=5):
        raise ValueError("Pick a time in the future.")
    end = start + timedelta(minutes=duration)
    title = (body.get("title") or "").strip()[:200] or default_title(lead)
    spec = {"start": start, "end": end, "title": title, "attendee_email": attendee or "",
            "notes": (body.get("notes") or "").strip()[:2000], "name": lead.get("contact_name", ""),
            "company": lead.get("company", "")}
    if fixed_link:
        created = {"join_url": booking_link(store, pid, lead.get("contact_name", ""), attendee or ""), "external_id": ""}
    else:
        created = create_event(store, pid, spec, transport, resolver)
    meeting = {"id": uuid.uuid4().hex[:12], "provider": pid, "title": title, "start": start.isoformat(),
               "end": end.isoformat(), "join_url": created.get("join_url", ""), "status": "scheduled",
               "created_at": now.isoformat(timespec="seconds"), "external_id": created.get("external_id", ""),
               "attendee_email": attendee or ""}
    if spec["notes"]:
        meeting["notes"] = spec["notes"]
    updated = _update_lead(store, lead_id, lambda l: {**l, "meetings": [*(l.get("meetings") or []), meeting],
                                                      "timeline": [*l["timeline"], _event("meeting", f"Meeting booked: {title}, {ist_label(start)}")]})
    invite = None
    if needs_our_invite(pid, body.get("send_invite")):
        invite = send_invite(store, updated, meeting, sender, "REQUEST")
        status = invite.get("status")
        meeting = {**meeting, "invite": status}
        updated = _update_lead(store, lead_id, lambda l: {
            **l, "meetings": [{**m, "invite": status} if m.get("id") == meeting["id"] else m for m in l.get("meetings") or []],
            "timeline": [*l["timeline"], _event("meeting", f"Calendar invite emailed to {invite.get('to')} ({status})"
                                                 if status not in ("skipped",) else f"No invite sent: {invite.get('reason')}")]})
    return {"kind": "event", "meeting": meeting, "lead": updated,
            "invite": {k: invite.get(k) for k in ("status", "to", "reason") if invite.get(k)} if invite else None}


def lead_meetings(store, lead_id: str) -> list:
    return sorted(find_lead(store, lead_id).get("meetings") or [], key=lambda m: m.get("start", ""))


def all_meetings(store, start: datetime = None, end: datetime = None) -> list:
    out = []
    for lead in store.read("leads"):
        for m in lead.get("meetings") or []:
            try:
                at = parse_time(m.get("start"))
            except ValueError:
                continue
            if (start and at < start) or (end and at > end):
                continue
            out.append({**m, "lead_id": lead["id"], "company": lead.get("company", ""), "contact_name": lead.get("contact_name", "")})
    return sorted(out, key=lambda m: parse_time(m["start"]))


def cancel(store, meeting_id: str, transport=request, resolver=None, sender=None) -> dict:
    """Cancel at the provider where possible, then mark it cancelled on the lead (and withdraw our invite)."""
    lead = next((l for l in store.read("leads") if any(m.get("id") == meeting_id for m in l.get("meetings") or [])), None)
    if lead is None:
        raise LookupError("Meeting not found")
    meeting = next(m for m in lead["meetings"] if m["id"] == meeting_id)
    if meeting.get("status") == "cancelled":
        return {"meeting": meeting, "message": "Already cancelled."}
    message = "Cancelled."
    if meeting["provider"] in REMOTE_CANCEL and meeting.get("external_id"):
        try:
            cancel_event(store, meeting["provider"], meeting["external_id"], transport, resolver)
            message = "Cancelled, and the invite was withdrawn in the calendar."
        except OSError as exc:
            if getattr(exc, "code", None) not in (404, 410):    # 404/410: already deleted in the calendar itself
                raise
            message = "Cancelled. It was already removed from the calendar."
    else:
        message = "Marked as cancelled here. Also cancel it in your calendar tool."

    def mark(l: dict) -> dict:
        meetings = [{**m, "status": "cancelled"} if m["id"] == meeting_id else m for m in l.get("meetings") or []]
        return {**l, "meetings": meetings, "timeline": [*l["timeline"], _event("meeting", f"Meeting cancelled: {meeting['title']}")]}
    updated = _update_lead(store, lead["id"], mark)
    if meeting.get("invite") in ("sent", "dry_run"):
        withdrawn = send_invite(store, updated, meeting, sender, "CANCEL")
        message += f" The lead was sent a cancellation ({withdrawn.get('status')})."
    return {"meeting": next(m for m in updated["meetings"] if m["id"] == meeting_id), "message": message}


def mark_done(store, meeting_id: str) -> dict:
    """The meeting happened: mark it done and close its no-show follow-up task, if one was created."""
    from ..activities import complete_task
    lead = next((l for l in store.read("leads") if any(m.get("id") == meeting_id for m in l.get("meetings") or [])), None)
    if lead is None:
        raise LookupError("Meeting not found")
    meeting = next(m for m in lead["meetings"] if m["id"] == meeting_id)
    if meeting.get("status") == "cancelled":
        raise ValueError("This meeting was cancelled.")
    updated = _update_lead(store, lead["id"], lambda l: {
        **l, "meetings": [{**m, "status": "done"} if m["id"] == meeting_id else m for m in l.get("meetings") or []],
        "timeline": [*l["timeline"], _event("meeting", f"Meeting done: {meeting.get('title', '')}")]})
    for task in store.read("tasks"):
        if task.get("meeting_id") == meeting_id and task.get("status", "open") == "open":
            complete_task(store, task["id"], "Meeting marked done")
    return next(m for m in updated["meetings"] if m["id"] == meeting_id)


# ---------- bookings from Cal.com / Calendly webhooks ----------
def parse_booking(pid: str, payload) -> list:
    """[{event: created|cancelled, emails[], name, title, start, end, join_url, external_id}]"""
    if not isinstance(payload, dict):
        return []
    if pid == "calcom":
        kind = {"BOOKING_CREATED": "created", "BOOKING_RESCHEDULED": "created", "BOOKING_CANCELLED": "cancelled"}.get(payload.get("triggerEvent"))
        data = payload.get("payload") or {}
        if not kind:
            return []
        attendees = [a for a in data.get("attendees") or [] if isinstance(a, dict)]
        location = str(data.get("location") or "")
        join = (data.get("metadata") or {}).get("videoCallUrl") or (location if location.startswith("https://") else "")
        return [{"event": kind, "emails": [a.get("email", "") for a in attendees], "name": (attendees[0].get("name", "") if attendees else ""),
                 "title": data.get("title", "Booked meeting"), "start": data.get("startTime", ""), "end": data.get("endTime", ""),
                 "join_url": join, "external_id": str(data.get("uid") or data.get("bookingId") or "")}]
    if pid == "calendly":
        kind = {"invitee.created": "created", "invitee.canceled": "cancelled"}.get(payload.get("event"))
        data = payload.get("payload") or {}
        if not kind:
            return []
        event = data.get("scheduled_event") or {}
        uri = str(event.get("uri") or data.get("event") or "")
        return [{"event": kind, "emails": [data.get("email", "")], "name": data.get("name", ""),
                 "title": event.get("name", "Booked meeting"), "start": event.get("start_time", ""), "end": event.get("end_time", ""),
                 "join_url": (event.get("location") or {}).get("join_url", ""), "external_id": uri.rstrip("/").rsplit("/", 1)[-1]}]
    return []


def _lead_for(leads: list, emails: list):
    wanted = [e for e in (normalise_email(x) for x in emails) if e]
    for email in wanted:
        for lead in leads:
            contacts = [normalise_email(c.get("email")) for c in lead.get("other_contacts") or [] if isinstance(c, dict)]
            if normalise_email(lead.get("email")) == email or email in contacts:
                return lead
    return None


def attach_booking(store, pid: str, booking: dict) -> dict:
    lead = _lead_for(store.read("leads"), booking.get("emails") or [])
    if lead is None:
        return {"matched": False}
    ext = booking.get("external_id", "")
    try:
        start = parse_time(booking.get("start"))
        label = ist_label(start)
    except ValueError:
        start, label = None, "time not given"
    try:
        end = parse_time(booking.get("end")).isoformat()     # '...Z' is unreadable to fromisoformat on Python 3.10
    except ValueError:
        end = ""

    def change(l: dict) -> dict:
        meetings = list(l.get("meetings") or [])
        index = next((i for i, m in enumerate(meetings) if ext and m.get("provider") == pid and m.get("external_id") == ext), None)
        if booking["event"] == "cancelled":
            if index is not None:
                meetings[index] = {**meetings[index], "status": "cancelled"}
            return {**l, "meetings": meetings, "timeline": [*l["timeline"], _event("meeting", f"Meeting cancelled by the lead ({pid})")]}
        record = {"provider": pid, "title": booking.get("title") or "Booked meeting", "start": start.isoformat() if start else "",
                  "end": end, "join_url": booking.get("join_url", ""), "status": "scheduled", "external_id": ext,
                  "attendee_email": normalise_email((booking.get("emails") or [""])[0]) or ""}
        if index is None:
            meetings.append({"id": uuid.uuid4().hex[:12], "created_at": now_utc().isoformat(timespec="seconds"), **record})
        else:
            meetings[index] = {**meetings[index], **record}
        return {**l, "meetings": meetings, "timeline": [*l["timeline"], _event("meeting", f"Meeting booked: {record['title']}, {label}")]}
    _update_lead(store, lead["id"], change)
    return {"matched": True, "lead_id": lead["id"]}
