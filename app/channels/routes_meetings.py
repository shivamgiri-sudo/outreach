"""Meetings API: book from a lead, list per lead, cancel, upcoming list, Google free/busy."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import meetings
from .calendar import free_busy

UPCOMING_GRACE = timedelta(hours=2)


class MeetingBody(BaseModel):
    provider: Optional[str] = None
    start: str = ""
    duration_minutes: int = Field(default=30, ge=meetings.MIN_MINUTES, le=meetings.MAX_MINUTES)
    title: str = ""
    attendee_email: str = ""
    notes: str = ""


def _time(text: str, label: str):
    if not text:
        return None
    try:
        return meetings.parse_time(text)
    except ValueError:
        raise HTTPException(400, f"'{label}' must be a date-time like 2026-11-05T10:00:00+05:30") from None


def make_router(store, transport, resolver=None, present=None) -> APIRouter:
    router = APIRouter()
    show = present or (lambda lead: lead)

    @router.post("/api/leads/{lead_id}/meetings")
    def book(lead_id: str, body: MeetingBody):
        try:
            result = meetings.book(store, lead_id, body.model_dump(), transport, resolver)
        except LookupError:
            raise HTTPException(404, "Lead not found")
        except meetings.NoCalendar as exc:
            raise HTTPException(409, str(exc))
        except PermissionError:
            raise HTTPException(400, "The calendar refused access. Reconnect it on the Integrations page.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except OSError as exc:
            raise HTTPException(502, f"The calendar did not accept the meeting: {str(exc)[:200]}")
        if result.get("lead"):
            result = {**result, "lead": show(result["lead"])}
        return result

    @router.get("/api/leads/{lead_id}/meetings")
    def lead_meetings(lead_id: str):
        try:
            return meetings.lead_meetings(store, lead_id)
        except LookupError:
            raise HTTPException(404, "Lead not found")

    @router.post("/api/meetings/{meeting_id}/cancel")
    def cancel(meeting_id: str):
        try:
            return meetings.cancel(store, meeting_id, transport, resolver)
        except LookupError:
            raise HTTPException(404, "Meeting not found")
        except PermissionError:
            raise HTTPException(400, "The calendar refused access. Reconnect it on the Integrations page.")
        except (OSError, ValueError) as exc:
            raise HTTPException(502, f"Could not cancel it in the calendar: {str(exc)[:200]}")

    @router.get("/api/meetings")
    def upcoming(from_: str = Query(default="", alias="from"), to: str = ""):
        begin = _time(from_, "from") or datetime.now(timezone.utc) - UPCOMING_GRACE
        return meetings.all_meetings(store, begin, _time(to, "to"))

    @router.get("/api/meetings/availability")
    def availability(start: str, end: str):
        begin, finish = _time(start, "start"), _time(end, "end")
        if not (begin and finish) or finish <= begin:
            raise HTTPException(400, "Give a start and an end time (end after start).")
        try:
            return {"busy": free_busy(store, begin, finish, transport)}
        except PermissionError:
            raise HTTPException(400, "Google Calendar refused access. Reconnect it on the Integrations page.")
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)[:200])

    return router
