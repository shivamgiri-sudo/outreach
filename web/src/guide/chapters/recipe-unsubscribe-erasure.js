// Recipe: handle an unsubscribe or a data-erasure request.
export default {
  id: "recipe-unsubscribe-erasure", title: "Handle an unsubscribe or a data-erasure request", group: "How-to recipes", order: 110, kind: "recipe",
  summary: "Stop contacting someone who asked, and (owner only) download or erase everything held on them under [[DPDP]].",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "An unsubscribe can be handled by anyone who can make changes (owner, admin, caller).",
      "Downloading a person's data and erasing it can only be done by an owner. Callers, admins and viewers do not see those buttons.",
      "Erasing cannot be undone. Be sure it is the right person and that the request is genuine.",
    ]],
    ["h", "A. Someone asks to stop (unsubscribe)"],
    ["p", "Every email the app sends has an unsubscribe link. When the person clicks it, or replies asking to stop, the app does the rest. You only need to act when the request came some other way, such as a phone call."],
    ["steps", [
      "Open {{menu:Leads}} and tick the person's row.",
      "In the bar that appears, click **Do not contact**, then **Yes, never contact them**.",
      "Check the lead: its email sequence is stopped, and the lead shows a **do-not-contact** mark.",
    ]],
    ["p", "What the app does by itself when someone unsubscribes by reply: [[Consent|consent]] is withdrawn, the stage becomes **Unsubscribed**, the sequence stops and the address goes on the do-not-contact list. A reply that asks you to delete their data also withdraws consent and creates a high-priority task called **Erasure request** (due in 24 hours). See {{menu:Tasks & follow-ups}}."],
    ["h", "B. Someone asks for a copy of their data"],
    ["steps", [
      "Owner: open {{menu:Leads}} and click the person to open their lead.",
      "Scroll to **Their data (DPDP)**. Click **Download their data**.",
      "A file named lead-(id).json is saved. It holds everything held on that person. Send it to them through a safe channel.",
    ]],
    ["h", "C. Someone asks you to delete their data (erasure)"],
    ["steps", [
      "Owner: open the person's lead and scroll to **Their data (DPDP)**.",
      "If they asked for a copy first, download it (part B).",
      "Click **Erase their data…**. A red box explains what will go: the lead, its emails, replies, messages, tasks and notes.",
      "Click **Yes, erase everything**. The lead closes.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The lead is gone from {{menu:Leads}}. Searching for the name or email finds nothing.",
      "Only a scrambled fingerprint of the email and phone is kept. If the same person turns up in a later import, they are left out. The import result counts them under rows left out because they are on the do-not-contact list or were erased at their request.",
      "The consent record is kept as proof, with the text removed. The erase is added to the change log with a reference and no name. See [read the audit trail](#help/recipe-audit-trail).",
    ]],
    ["warn", "Erasure deletes the lead and its history for good. Nothing can bring it back, other than a backup restore, which would also undo other recent work. If a person only wants fewer emails, use **Do not contact** instead."],
    ["tip", "A person who is on the do-not-contact list or has been erased is also refused when a lead-capture form or a connector tries to add them again. They show as **Refused (opted out or erased)** in Recent captures."],
  ],
};
