// Page chapter: Meetings (and booking from a lead panel).
export default {
  id: "meetings", title: "Meetings", group: "Menu pages", order: 160, kind: "page", menu: "Meetings",
  summary: "See upcoming demos and calls by day, and learn how to book one from a lead's panel.",
  blocks: [
    ["p", "{{menu:Meetings}} lists the demos and calls that are booked, grouped by day. All times are shown in [[IST]]. The line under the title says which calendar you book through, for example Google Calendar. You do not book on this page: you book from a lead's panel (see below)."],
    ["h", "Who can use it"],
    ["ul", [
      "Everyone can read the list and click **Join**.",
      "Owners, admins and callers can book, mark done and cancel from a lead's panel. Viewers cannot.",
      "Connecting a calendar is done by an owner on {{menu:Integrations}}.",
    ]],
    ["h", "Read the list"],
    ["ul", [
      "Each day is a card. Each row shows the time, the company (click it to open the lead), the contact, the title, the calendar provider and a status: scheduled, done or cancelled.",
      "**Join** opens the video link. It only shows for scheduled meetings that have a link.",
      "Cancelled meetings are hidden. Tick **Show cancelled** to see them.",
      "**Download all meetings** saves a [[CSV]] of past, upcoming and cancelled meetings with times in IST.",
    ]],
    ["tip", "If you see \"No calendar connected yet\", click **Open Integrations** and set up Google Calendar, Microsoft 365, Zoom or a booking page such as Cal.com or Calendly. Without one you cannot get a video link."],
    ["h", "Book a meeting from a lead"],
    ["steps", [
      "Open the lead from {{menu:Leads}} (or click a company anywhere). Scroll to the **Meetings** section.",
      "Click **Book a meeting** (the calendar name is in brackets). The button is off for leads who unsubscribed.",
      "Pick the **Date (IST)**, **Time (IST)** and **Duration** (15, 30, 45 or 60 minutes). Check the **Title** and **Attendee email**. Notes are optional.",
      "If the time is outside Monday to Saturday, 09:00 to 18:00 IST, the form warns you. You can still go ahead.",
      "Click **Book meeting**. The meeting appears in the lead's list and on this page.",
    ]],
    ["h", "Calendar types behave differently"],
    ["table", { head: ["Calendar", "What happens"], rows: [
      ["Google Calendar or Microsoft 365", "The calendar emails the lead an invite itself. Tick **Send our own invite too** only if you want a second one."],
      ["Zoom, custom link, or a booking page with a fixed time", "The portal emails the lead a calendar invite (.ics) with the join link. With real sending off, it is kept as practice."],
      ["Booking page (Cal.com, Calendly) without a fixed time", "Click **Get booking link**, then **Copy link** and send it to the lead. They pick a slot. Some pages report the booking back by themselves; if not, check your calendar."],
    ] }],
    ["h", "After the meeting"],
    ["ul", [
      "**Mark done** shows once the start time has passed.",
      "**Cancel** asks you to confirm with **Yes, cancel it**. Use **Keep** to back out.",
    ]],
    ["p", "Related: [Leads](#help/leads), [Integrations](#help/integrations), [Tasks & follow-ups](#help/tasks)."],
  ],
};
