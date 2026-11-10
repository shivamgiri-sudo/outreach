// Recipe: export data.
export default {
  id: "recipe-export-data", title: "Export your data", group: "How-to recipes", order: 90, kind: "recipe",
  roles: ["owner"],
  summary: "Download leads, tasks, emails, meetings and more as files that open in Excel, and a full backup.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only owners see the download buttons. Admins, callers and viewers do not, because the files hold personal data. If you need a file, ask an owner.",
      "Files are [[CSV]] files that open in Excel. They start with a mark that makes Indian names and the rupee sign show correctly.",
      "Times in the files come twice: a readable column ending in _ist (India time) and the raw value.",
      "Keys, secrets and tokens are never in these files.",
    ]],
    ["h", "Where the buttons are"],
    ["table", { head: ["Page", "Button", "What you get"], rows: [
      ["{{menu:Leads}}", "**Download all**, **Download these N**, and **Download CSV** after you tick rows", "Every lead, only the leads that match your search and filters, or only the ticked leads."],
      ["{{menu:Tasks & follow-ups}}", "**Download CSV**", "The tasks you are looking at (owner and bucket filters apply)."],
      ["{{menu:Outbox & sending}}", "**Emails**, **Emails with full text**, **Replies**, **WhatsApp**, **Reminders & invites**", "Every email with when, to whom, step, subject and status; replies with the lead each matched."],
      ["{{menu:Meetings}}", "**Download all meetings**", "All meetings."],
      ["{{menu:Lists}}", "**Download lists**", "Every list with open, reply and accept rates."],
      ["{{menu:Insights}}", "**Download insights**", "The insights tables."],
      ["{{menu:Data hygiene}}", "**Download duplicates**", "The groups of probable duplicates."],
      ["{{menu:Reports}}", "**Download activity log (CSV)**", "Owners and workspace admins only. The calls and messages for the period."],
      ["{{menu:Audit}}", "**Download CSV**, **Audit pack (zip)**", "The change log. See [read the audit trail](#help/recipe-audit-trail)."],
      ["{{menu:Backup & restore}}", "**Download backup**", "Everything in one file, for moving or going back. Not for spreadsheets."],
    ] }],
    ["h", "Steps (example: leads)"],
    ["steps", [
      "Open {{menu:Leads}}.",
      "Use the search box and filters to narrow the list, if you want only some leads.",
      "Click **Download all** for everything, or **Download these N** for what is on screen. To export hand-picked leads, tick them and click **Download CSV** in the bar that appears.",
      "The button says **Preparing…**. Wait. The file then saves to your Downloads folder.",
      "Open it in Excel.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "A file is saved and opens with the columns you expect. If a red message appears next to the button, read it; it says what went wrong.",
      "Every download is recorded in the change log: who, which list, how many rows and which filters. Look under **Downloads (CSV/JSON)** in {{menu:Audit}}.",
    ]],
    ["h", "Backup"],
    ["steps", [
      "Open {{menu:Backup & restore}} in Setup.",
      "Click **Download backup**. This file leaves out connection keys, password hashes and pairing tokens. After a restore you enter the keys again on {{menu:Integrations}}.",
      "Only if you are moving to a new server, tick **Also include connection keys, login password hashes, tokens and secrets** and click **Download backup with keys**.",
    ]],
    ["warn", "The backup with keys holds API keys and mailbox passwords in plain text. Anyone who gets it can send email and WhatsApp as you. Store it like a password, never email or chat it, and delete it when done. Other files hold personal data of real people, so keep them private too (see [DPDP](#help/glossary/DPDP))."],
  ],
};
