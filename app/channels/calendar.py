"""Calendar adapters: create / cancel meetings, booking links, free/busy and connection tests.

Event-based: Google Calendar (+ Meet), Microsoft 365 (+ Teams), Zoom, custom HTTP.
Link-based: Cal.com, Calendly, any booking link (the lead picks the slot; a webhook brings the booking back).
"""
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from .config import config_for, set_state
from .custom_http import call_custom
from .http import request
from .netguard import check_public_url
from .oauth import access_token, zoom_token

GCAL = "https://www.googleapis.com/calendar/v3"
GRAPH = "https://graph.microsoft.com/v1.0"
ZOOM_API = "https://api.zoom.us/v2"
CALENDLY_API = "https://api.calendly.com"
CALCOM_VERSION_BOOKINGS = "2024-08-13"
CALCOM_VERSION_EVENT_TYPES = "2024-06-14"
IST = "Asia/Kolkata"
EVENT_PROVIDERS = ("google", "microsoft", "zoom", "custom_http")
LINK_PROVIDERS = ("calcom", "calendly", "booking_link")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _google_create(store, values, spec, transport):
    token = access_token(store, "google", transport)
    cal = quote(values.get("calendar_id") or "primary", safe="@.")
    body = {"summary": spec["title"], "description": spec.get("notes", ""),
            "start": {"dateTime": spec["start"].isoformat(), "timeZone": IST},
            "end": {"dateTime": spec["end"].isoformat(), "timeZone": IST},
            "attendees": [{"email": spec["attendee_email"]}] if spec.get("attendee_email") else [],
            "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                                 "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
    reply = transport(f"{GCAL}/calendars/{cal}/events?conferenceDataVersion=1&sendUpdates=all", _bearer(token), body)
    entry = next((e.get("uri") for e in (reply.get("conferenceData") or {}).get("entryPoints", [])
                  if e.get("entryPointType") == "video"), "")
    return {"external_id": reply.get("id", ""), "join_url": reply.get("hangoutLink") or entry or reply.get("htmlLink", "")}


def _microsoft_create(store, values, spec, transport):
    token = access_token(store, "microsoft", transport)
    fmt = "%Y-%m-%dT%H:%M:%S"
    body = {"subject": spec["title"], "body": {"contentType": "text", "content": spec.get("notes", "")},
            "start": {"dateTime": _utc(spec["start"]).strftime(fmt), "timeZone": "UTC"},
            "end": {"dateTime": _utc(spec["end"]).strftime(fmt), "timeZone": "UTC"},
            "attendees": ([{"emailAddress": {"address": spec["attendee_email"]}, "type": "required"}]
                          if spec.get("attendee_email") else []),
            "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness"}
    reply = transport(f"{GRAPH}/me/events", _bearer(token), body)
    return {"external_id": reply.get("id", ""),
            "join_url": (reply.get("onlineMeeting") or {}).get("joinUrl") or reply.get("webLink", "")}


def _zoom_create(store, values, spec, transport):
    token = zoom_token(store, transport)
    minutes = int((spec["end"] - spec["start"]).total_seconds() // 60)
    body = {"topic": spec["title"], "type": 2, "start_time": _utc(spec["start"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": minutes, "timezone": IST, "agenda": spec.get("notes", "")[:2000],
            "settings": {"waiting_room": True, "join_before_host": False}}
    reply = transport(f"{ZOOM_API}/users/me/meetings", _bearer(token), body)
    return {"external_id": str(reply.get("id", "")), "join_url": reply.get("join_url", "")}


def _custom_create(values, spec, transport, resolver):
    variables = {"title": spec["title"], "start": spec["start"].isoformat(), "end": spec["end"].isoformat(),
                 "email": spec.get("attendee_email", ""), "name": spec.get("name", ""), "company": spec.get("company", ""),
                 "text": spec.get("notes", "")}
    result = call_custom(values, variables, transport, resolver)
    if not result["ok"]:
        raise OSError(result.get("error", "The custom calendar API did not say success."))
    return {"external_id": result.get("provider_id", ""), "join_url": result.get("join_url", "")}


def create_event(store, pid: str, spec: dict, transport=request, resolver=None) -> dict:
    """spec = {start, end (aware datetimes), title, attendee_email?, notes?, name?, company?}."""
    cfg = config_for(store, "calendar", pid)
    if not cfg:
        raise ValueError("This calendar is not set up yet.")
    if pid == "google":
        return _google_create(store, cfg["values"], spec, transport)
    if pid == "microsoft":
        return _microsoft_create(store, cfg["values"], spec, transport)
    if pid == "zoom":
        return _zoom_create(store, cfg["values"], spec, transport)
    if pid == "custom_http":
        return _custom_create(cfg["values"], spec, transport, resolver)
    raise ValueError("This provider shares a booking link instead of creating events.")


def cancel_event(store, pid: str, external_id: str, transport=request, resolver=None) -> None:
    cfg = config_for(store, "calendar", pid)
    if not cfg or not external_id:
        raise ValueError("This meeting cannot be cancelled from here; cancel it in the calendar itself.")
    values, ext = cfg["values"], quote(str(external_id), safe="")
    if pid == "google":
        cal = quote(values.get("calendar_id") or "primary", safe="@.")
        transport(f"{GCAL}/calendars/{cal}/events/{ext}?sendUpdates=all", _bearer(access_token(store, "google", transport)),
                  None, method="DELETE")
    elif pid == "microsoft":
        transport(f"{GRAPH}/me/events/{ext}/cancel", _bearer(access_token(store, "microsoft", transport)),
                  {"comment": "Cancelled from Outreach OS"})
    elif pid == "zoom":
        transport(f"{ZOOM_API}/meetings/{ext}", _bearer(zoom_token(store, transport)), None, method="DELETE")
    elif pid == "calcom" and values.get("api_key"):
        base = check_public_url(values.get("api_base") or "https://api.cal.com/v2", resolver).rstrip("/")
        transport(f"{base}/bookings/{ext}/cancel", {**_bearer(values["api_key"]), "cal-api-version": CALCOM_VERSION_BOOKINGS},
                  {"cancellationReason": "Cancelled from Outreach OS"})
    elif pid == "calendly":
        transport(f"{CALENDLY_API}/scheduled_events/{ext}/cancellation", _bearer(values["access_token"]),
                  {"reason": "Cancelled from Outreach OS"})
    else:
        raise ValueError("This meeting cannot be cancelled from here; cancel it in the calendar itself.")


def _with_query(url: str, extra: dict) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: v for k, v in extra.items() if v})
    return urlunparse(parts._replace(query=urlencode(query)))


def booking_link(store, pid: str, name: str = "", email: str = "") -> str:
    cfg = config_for(store, "calendar", pid)
    if not cfg:
        raise ValueError("This booking link is not set up yet.")
    values = cfg["values"]
    if pid == "booking_link":
        url = values["booking_url"]
        if "{name}" in url or "{email}" in url:
            return url.replace("{name}", quote(name or "")).replace("{email}", quote(email or ""))
        return url
    url = values.get("booking_url") or (cfg["record"].get("state") or {}).get("scheduling_url", "")
    if not url:
        raise ValueError("Add your booking link, or click Test so the app can read it from your account.")
    return _with_query(url, {"name": name, "email": email})


def free_busy(store, start: datetime, end: datetime, transport=request) -> list:
    """Busy blocks in the Google calendar between start and end: [{start, end}]."""
    cfg = config_for(store, "calendar", "google")
    if not cfg:
        raise ValueError("Connect Google Calendar to check free / busy times.")
    cal = cfg["values"].get("calendar_id") or "primary"
    body = {"timeMin": _utc(start).isoformat(), "timeMax": _utc(end).isoformat(), "timeZone": IST, "items": [{"id": cal}]}
    reply = transport(f"{GCAL}/freeBusy", _bearer(access_token(store, "google", transport)), body)
    found = (reply.get("calendars") or {}).get(cal) or {}
    if found.get("errors"):                  # e.g. notFound for a wrong calendar id: not the same as "free all day"
        reasons = ", ".join(str(e.get("reason", "")) for e in found["errors"] if isinstance(e, dict))
        raise ValueError(f"Google could not read calendar '{cal}': {reasons or 'error'}.")
    return list(found.get("busy") or [])


def test_calendar(store, pid: str, transport=request, resolver=None, webhook_url: str = "", webhook_secret: str = "") -> dict:
    cfg = config_for(store, "calendar", pid)
    if not cfg:
        return {"ok": False, "message": "Save all the required details first."}
    values = cfg["values"]
    if pid == "google":
        now = datetime.now(timezone.utc).isoformat()
        cal = quote(values.get("calendar_id") or "primary", safe="@.")
        reply = transport(f"{GCAL}/calendars/{cal}/events?maxResults=1&singleEvents=true&timeMin={quote(now)}",
                          _bearer(access_token(store, "google", transport)), None, method="GET")
        return {"ok": True, "message": f"Connected to Google Calendar '{reply.get('summary', cal)}'. Meet links will be added."}
    if pid == "microsoft":
        reply = transport(f"{GRAPH}/me/calendars?$top=5", _bearer(access_token(store, "microsoft", transport)), None, method="GET")
        names = ", ".join(c.get("name", "") for c in reply.get("value", [])) or "your calendar"
        return {"ok": True, "message": f"Connected to Microsoft 365: {names}."}
    if pid == "zoom":
        reply = transport(f"{ZOOM_API}/users/me", _bearer(zoom_token(store, transport)), None, method="GET")
        return {"ok": True, "message": f"Connected to Zoom as {reply.get('email', 'your account')}."}
    if pid == "calcom":
        if not values.get("api_key"):
            return {"ok": True, "message": f"Booking link saved: {values['booking_url']}. Add an API key to list event types."}
        base = check_public_url(values.get("api_base") or "https://api.cal.com/v2", resolver).rstrip("/")
        reply = transport(f"{base}/event-types", {**_bearer(values["api_key"]), "cal-api-version": CALCOM_VERSION_EVENT_TYPES},
                          None, method="GET")
        data = reply.get("data") or []
        titles = ", ".join(str(e.get("title") or e.get("slug")) for e in data[:5] if isinstance(e, dict))
        return {"ok": True, "message": f"Cal.com works. Event types: {titles or 'none found'}."}
    if pid == "calendly":
        return _calendly_test(store, values, transport, webhook_url, webhook_secret)
    if pid == "booking_link":
        return {"ok": True, "message": "Link looks right. For a sample lead it becomes: "
                + booking_link(store, pid, "Test Lead", "test.lead@example.com")}
    if pid == "custom_http":
        start = datetime.now(timezone.utc) + timedelta(days=1)
        result = call_custom(values, {"title": "Outreach OS test", "start": start.isoformat(),
                                      "end": (start + timedelta(minutes=30)).isoformat(), "email": "test.lead@example.com",
                                      "name": "Test Lead", "company": "Example Pvt Ltd", "text": "Test"}, transport, resolver)
        return {"ok": result["ok"], "message": "The calendar API said success." if result["ok"] else result.get("error", ""),
                "preview": result.get("preview"), "response": result.get("response")}
    return {"ok": False, "message": "Unknown calendar provider."}


def _calendly_test(store, values, transport, webhook_url, webhook_secret) -> dict:
    me = (transport(f"{CALENDLY_API}/users/me", _bearer(values["access_token"]), None, method="GET").get("resource") or {})
    set_state(store, "calendar", "calendly", scheduling_url=me.get("scheduling_url", ""))
    message = f"Connected to Calendly as {me.get('name', 'your account')}."
    if not (webhook_url and me.get("uri") and me.get("current_organization")):
        return {"ok": True, "message": message}
    body = {"url": webhook_url, "events": ["invitee.created", "invitee.canceled"], "organization": me["current_organization"],
            "user": me["uri"], "scope": "user", "signing_key": webhook_secret}
    try:
        transport(f"{CALENDLY_API}/webhook_subscriptions", _bearer(values["access_token"]), body)
    except PermissionError:
        return {"ok": True, "message": message + " Booking webhooks need a paid Calendly plan; the booking link still works."}
    except OSError as exc:
        if "409" in str(exc):
            return {"ok": True, "message": message + " The booking webhook was already registered."}
        return {"ok": True, "message": message + f" Could not register the booking webhook ({str(exc)[:120]})."}
    set_state(store, "calendar", "calendly", webhook_registered=True)
    return {"ok": True, "message": message + " Booking webhook registered: new bookings will appear on the lead."}
