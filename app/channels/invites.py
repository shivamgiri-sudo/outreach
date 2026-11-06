"""Calendar invites (.ics, RFC 5545) emailed to the lead when the meeting provider does not send one itself.

Google Calendar and Microsoft 365 email the attendee themselves, and Cal.com / Calendly confirm bookings the lead
makes, so they get our invite only when the owner ticks "Send our own invite". Zoom, a custom calendar API and a
plain booking link used with a fixed time always get one. Cancelling sends METHOD:CANCEL with the same UID.

The invite goes through the transactional sender (one-to-one, not the cold-email outbox or its daily cap) and is
recorded in `transactional_outbox` with its .ics text. In practice mode ("Send for real" off) nothing is sent and
the row is kept as dry_run.
"""
from datetime import datetime, timedelta, timezone

from ..privacy import is_blocked
from ..reminders import _log_transactional, ist_text
from ..brand import get_brand, sender_name
from ..sequences import identity_footer
from ..unsubscribe import unsub_url

OWN_INVITE = ("zoom", "custom_http", "booking_link")          # these never email the attendee themselves
PRODID = "-//BookMyInterview.ai//Outreach OS//EN"
DEFAULT_ORGANIZER = "no-reply@outreach.local"


def needs_our_invite(provider: str, send_own: bool = False) -> bool:
    return bool(send_own) or provider in OWN_INVITE


def _text(value) -> str:
    """Escape a TEXT value: backslash, semicolon, comma and line breaks."""
    return (str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", ""))


def _param(value) -> str:
    """A parameter value such as CN: quoted, with quotes and line breaks removed."""
    return '"' + str(value or "").replace('"', "'").replace("\r", " ").replace("\n", " ") + '"'


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after CRLF + space (never splitting a UTF-8 character)."""
    out, current = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode("utf-8"))
            current = b""
        current += encoded
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def _utc(value) -> str:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def meeting_end(meeting: dict):
    start = datetime.fromisoformat(meeting["start"])
    try:
        return datetime.fromisoformat(meeting["end"]) if meeting.get("end") else start + timedelta(minutes=30)
    except ValueError:
        return start + timedelta(minutes=30)


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def build_ics(meeting: dict, organizer_email: str, organizer_name: str, attendee_email: str, attendee_name: str = "",
              method: str = "REQUEST", sequence: int = 0, now: datetime = None, domain: str = "outreach.local",
              product: str = "BookMyInterview.ai") -> str:
    join = _one_line(meeting.get("join_url"))
    cancelled = method == "CANCEL"
    description = "\n".join(p for p in (f"Join: {join}" if join else "", meeting.get("notes") or "",
                                        f"Booked from Outreach OS ({product}).") if p)
    lines = [
        "BEGIN:VCALENDAR", f"PRODID:{PRODID}", "VERSION:2.0", "CALSCALE:GREGORIAN", f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{meeting['id']}@{domain}",
        f"DTSTAMP:{_utc(now or datetime.now(timezone.utc))}",
        f"DTSTART:{_utc(meeting['start'])}",
        f"DTEND:{_utc(meeting_end(meeting))}",
        f"SEQUENCE:{sequence}",
        f"SUMMARY:{_text(meeting.get('title') or 'Meeting')}",
        f"DESCRIPTION:{_text(description)}",
        *([f"LOCATION:{_text(join)}", f"URL:{join}"] if join else []),
        f"ORGANIZER;CN={_param(organizer_name or organizer_email)}:mailto:{organizer_email}",
        f"ATTENDEE;CN={_param(attendee_name or attendee_email)};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "TRANSP:OPAQUE",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _organizer(store) -> tuple:
    from ..senders import email_ready
    cfg = email_ready(store) or {}
    settings = store.read("settings")
    return (cfg.get("from_email") or DEFAULT_ORGANIZER,
            cfg.get("from_name") or sender_name(settings))


def _body(store, lead: dict, meeting: dict, cancel: bool, organizer_name: str) -> str:
    first = (lead.get("contact_name") or "").split(" ")[0] or "there"
    when = ist_text(meeting["start"])
    title = meeting.get("title") or "our call"
    if cancel:
        lines = [f"Hi {first},", "", f"Our meeting \"{title}\" on {when} is cancelled. Sorry for the change.",
                 "If you'd still like to talk, just reply and we'll find another time."]
    else:
        lines = [f"Hi {first},", "", f"You're invited: {title}", f"When: {when}"]
        if meeting.get("join_url"):
            lines.append(f"Join: {meeting['join_url']}")
        if meeting.get("notes"):
            lines += ["", meeting["notes"]]
        lines += ["", "The calendar invite is attached. If the time doesn't work, just reply and we'll find another slot."]
    lines += ["", f"— {organizer_name}"]
    footer = identity_footer(store.read("settings"))
    return "\n".join(lines + ([footer] if footer else []) + [f"Unsubscribe: {unsub_url(store, '', lead['id'])}"])


def send_invite(store, lead: dict, meeting: dict, sender=None, method: str = "REQUEST") -> dict:
    """Email the .ics invite (or its cancellation) to the lead. Returns the transactional_outbox row, or a skip."""
    to = meeting.get("attendee_email") or lead.get("email")
    if not to:
        return {"status": "skipped", "reason": "no attendee email"}
    if lead.get("stage") == "unsubscribed" or is_blocked(store, to):
        return {"status": "skipped", "reason": "the lead unsubscribed or is on the do-not-contact list"}
    organizer_email, organizer_name = _organizer(store)
    cancel = method == "CANCEL"
    ics = build_ics(meeting, organizer_email, organizer_name, to, lead.get("contact_name", ""), method,
                    sequence=1 if cancel else 0, domain=organizer_email.rpartition("@")[2] or "outreach.local",
                    product=get_brand(store)["product_name"])
    title = _one_line(meeting.get("title")) or "our meeting"
    subject = f"Cancelled: {title}" if cancel else f"Invitation: {title} — {ist_text(meeting['start'])}"
    message = {"to": to, "subject": subject, "body": _body(store, lead, meeting, cancel, organizer_name),
               "kind": "meeting_cancel" if cancel else "meeting_invite", "ics": ics, "ics_method": method}
    live = bool(store.read("settings").get("live_sending"))
    result = sender(message) if (live and sender) else {"status": "dry_run"}
    return _log_transactional(store, to, subject, message["body"], message["kind"], lead["id"], result,
                              extra={"ics": ics, "meeting_id": meeting["id"]})
