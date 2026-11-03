# QA: calendar & meetings

Branch `qa/calendar-meetings`. Tests: `tests/qa/test_calendar-meetings.py` (providers, OAuth, booking from the lead
panel API), `tests/qa/test_calendar-meetings_webhooks.py` (Cal.com / Calendly webhooks),
`tests/qa/test_calendar-meetings_invites_reminders.py` (.ics invites, reminders, no-show). Shared fakes are in
`tests/qa/calendar_meetings_fakes.py` and fixtures in `tests/qa/fixtures/calendar-meetings/`. 31 tests. Every HTTP call goes
to `FakeHTTP`, so nothing touches the network. All people, companies, domains and phone numbers are made up.

## Formats and sources

| Area | What was copied into the fixtures and asserted | Source |
|---|---|---|
| Google OAuth web-server flow | auth URL params (`access_type=offline`, `prompt=consent`), code exchange form, refresh form, `400 invalid_grant` on a revoked refresh token | https://developers.google.com/identity/protocols/oauth2/web-server |
| Google Calendar events.insert + Meet | `conferenceDataVersion=1`, `conferenceData.createRequest{requestId, conferenceSolutionKey.type=hangoutsMeet}`, `hangoutLink`, `entryPoints[video]`, `status.statusCode=pending` | https://developers.google.com/workspace/calendar/api/v3/reference/events/insert, https://developers.google.com/workspace/calendar/api/guides/create-events#conferencing |
| Google freeBusy.query | `timeMin/timeMax/timeZone/items`, `calendars[id].busy[]`, `calendars[id].errors[{reason: notFound}]` | https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query |
| Google delete event | `410 Gone` for an event that was already deleted | https://developers.google.com/workspace/calendar/api/guides/errors |
| Microsoft identity auth-code flow | `/{tenant}/oauth2/v2.0/authorize` and `/token`, `response_mode=query`, `offline_access` scope | https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow |
| Microsoft Graph event with Teams | `POST /me/events` with `isOnlineMeeting`, `onlineMeetingProvider=teamsForBusiness`, `onlineMeeting.joinUrl`; `POST /events/{id}/cancel` → 202 | https://learn.microsoft.com/en-us/graph/api/user-post-events, https://learn.microsoft.com/en-us/graph/api/event-cancel |
| Zoom Server-to-Server OAuth | `POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id=…` with Basic client auth; 1 h token, no refresh token | https://developers.zoom.us/docs/internal-apps/s2s-oauth/ |
| Zoom create / delete meeting | `POST /users/me/meetings` (`type 2`, `start_time` in UTC `Z`, `duration`), `join_url`, numeric `id`; error `3001 Meeting does not exist` (404) | https://developers.zoom.us/docs/api/meetings/#tag/meetings/POST/users/{userId}/meetings |
| Cal.com webhooks | `BOOKING_CREATED / RESCHEDULED / CANCELLED`, `payload.uid`, `startTime/endTime` with `Z`, `metadata.videoCallUrl`, `rescheduleUid` (the old booking), `X-Cal-Signature-256` = HMAC-SHA256 of the raw body | https://cal.com/docs/developing/guides/automation/webhooks |
| Calendly webhooks | `invitee.created / invitee.canceled`, `scheduled_event.uri/start_time/location.join_url`, `rescheduled` + `new_invitee`; `Calendly-Webhook-Signature: t=…,v1=…` over `"<t>.<body>"` | https://developer.calendly.com/api-docs/webhooks, https://developer.calendly.com/api-docs/calendly-api/users/get-current-user |
| iCalendar invites | `METHOD:REQUEST/CANCEL`, same `UID` on cancel, `SEQUENCE` bump, UTC `…Z` times, CRLF line ends, folding at 75 octets, TEXT escaping | RFC 5545 https://www.rfc-editor.org/rfc/rfc5545, RFC 5546 https://www.rfc-editor.org/rfc/rfc5546 |

## What was simulated

- **Leads:** a 105-row Apollo-style CSV with a BOM, ALL CAPS names and companies, "Pvt. Ltd." / "(OPC) PVT LTD", +91, 0 and
  spaced phones, a duplicate row, and other contacts at the same domain. There are 60 companies and 92 people.
- **Booking from the lead panel:** Google (on a shared calendar id, a time with no zone read as IST, a new `requestId`
  per meeting, a pending Meet link), Microsoft (UTC body, Teams link, cancel endpoint, tenant validation), Zoom (a
  token cached for an hour, a meeting across IST midnight, a 2,000-character agenda cap, 401 / 400 / 429 errors that
  save nothing), Cal.com / Calendly / any booking link (pre-filled links, a fixed-time booking link that becomes a
  meeting with our invite), and roles (a viewer gets 403, a caller can book and cancel, only the owner can connect OAuth).
- **Webhooks:** 92 bookings with messy attendee email cases and spaces, matched to primary and other contacts; 25
  strangers; odd triggers; malformed bodies; signature and token rules; Calendly's 5-minute tolerance; a reschedule
  pair; a retried delivery after a cancel; and an unsubscribed lead (the booking is kept, but only the owner is
  reminded).
- **Invites:** in practice mode the invite and its cancellation are kept as `dry_run` rows. In live mode they go
  through the sender. Google gets an invite only when "Send our own invite" is ticked. A suppressed or unsubscribed
  lead gets nothing.
- **Reminders:** two days of timer ticks every 10 minutes over five meetings: a 00:15 IST meeting (the day before in UTC),
  a morning meeting, a cancelled one, a done one, and one booked by an other contact. Each scheduled meeting gets
  exactly one 24 h email, one 1 h email and one 15-minute owner heads-up. Cancelled and done meetings get nothing. Each
  meeting that was left open gets exactly one no-show task, which closes when the meeting is marked done. Also checked:
  the timer starting late, the 7-day no-show window, and a 90-minute meeting's real end.

## Bugs found and fixed (one commit each, smallest change)

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | high | **Meeting invites and reminder emails to a lead had no unsubscribe link.** This breaks PRD §4.4 ("every message has an unsubscribe"). `base_url` was passed to the reminder loop but never used. | `app/reminders.py` and `app/channels/invites.py` now add `Unsubscribe: <signed /u/ link>` to the body. |
| 2 | high | **Cal.com `BOOKING_RESCHEDULED` left the old booking scheduled**, because it has a new `uid` and puts the old one in `rescheduleUid`. The lead was reminded about a slot that no longer existed, and the owner got a no-show task for it. | `parse_booking` reads `rescheduleUid`, and `attach_booking` cancels that meeting. |
| 3 | high | **A retried or out-of-order `BOOKING_CREATED` reopened a cancelled meeting**, which also put it back into reminders. This breaks the "closed stays closed" rule. | A delivery for a cancelled meeting with the same id changes nothing. |
| 4 | medium | **The booking end time from a webhook was stored raw (`…Z`).** Python 3.10's `fromisoformat` can't read it, so `meeting_end` fell back to start + 30 minutes. For a 60-minute booking the no-show task fired as the meeting ended. | The end is stored through `parse_time`. |
| 5 | medium | **Reminders went to the lead's primary email even when an other contact booked.** The invite went to the attendee, but the "Hi Sneha" reminder went to Sneha when Manoj had booked. | Reminders use `meeting.attendee_email`, greet that person, and check suppression on that address. |
| 6 | medium | **A revoked or expired refresh token (`400 invalid_grant`) gave a 502** "calendar did not accept the meeting" instead of asking the owner to reconnect. | `refresh_tokens` raises `PermissionError`, so the API returns 400 "Reconnect". |
| 7 | medium | **Cancelling a meeting already deleted in the calendar** (Google 410, Zoom or Graph 404) **gave a 502 and left it scheduled**, so reminders kept firing. | A 404 or 410 counts as already cancelled. Other errors still stop the cancel so it can be retried. |
| 8 | low | **Free/busy showed "all free" for a wrong calendar id.** Google returns `errors: [notFound]` with an empty `busy` list. | `free_busy` raises the error, and the API returns 400 with the reason. |

## Product limitations (not changed) and recommendations

- **Same-day 24 h reminder wording.** A meeting booked more than 2 hours but less than 24 hours ahead gets its "24 h"
  email on the next tick, saying "our call tomorrow" even when the call is today. Recommendation: say "today" or
  "tomorrow" by comparing IST dates, or skip the 24 h email when the booking was made less than 24 hours before the start.
- **Webhook rate limit:** 120 requests per minute per address on `/api/webhooks/`. Replaying a month of bookings in
  one burst would get 429s; the volume test lifts the limiter. Real Cal.com / Calendly traffic is far below this.
  Recommendation: give webhooks their own budget per provider if bulk replays are ever needed.
- **Pending Google Meet.** When `conferenceData.createRequest.status` is `pending`, the join link falls back to the
  event's `htmlLink`. Recommendation: re-read the event once (`events.get`) after a few seconds.
- **Free/busy is Google only.** Microsoft (`/me/calendar/getSchedule`) and Zoom have no availability check.
- **No reschedule of our own events.** Moving a booked meeting means cancelling and booking again, which gives a new
  UID. The .ics `SEQUENCE` is always 0 for the request and 1 for the cancel.
- **Calendly reschedules arrive as a pair** (canceled with `rescheduled: true`, then created). The app handles each
  event on its own, which works but gives two timeline lines.
- **The custom HTTP email connector can't attach the `.ics` file** (already in the PRD).

## Result

Full suite: 878 passed (`python -m pytest -q`).
