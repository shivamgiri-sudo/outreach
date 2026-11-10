// Recipe: read the audit trail.
export default {
  id: "recipe-audit-trail", title: "Read the audit trail", group: "How-to recipes", order: 120, kind: "recipe",
  roles: ["owner"],
  summary: "See who changed what and when, check the log has not been tampered with, and download an audit pack for a lead or a date range.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only owners can open {{menu:Audit}} in Setup and download from it.",
      "Times are shown in India time ([[IST]]).",
      "The log keeps the most recent 20000 rows. Older rows drop off when it gets longer. The check below tells you when that has happened, so you can tell it from tampering.",
    ]],
    ["p", "The log is a [[Change log|change log]]. Every row is linked to the one before it by a [[Hash chain|hash chain]], so an edit or a deletion shows up in the check."],
    ["h", "Find a change"],
    ["steps", [
      "Open {{menu:Audit}} in Setup.",
      "Pick an **Action** group, for example **Stage changes**, **Send for real**, **Bulk actions**, **Erasures**, **Logins and roles**, **Workspaces**, **LinkedIn agent**, **Settings saved**, **Downloads (CSV/JSON)**, **Backups and restores**, **Imports undone**, **AI-drafted messages** or **Audit packs**.",
      "Pick a **Person** (or leave **Anyone**). Type part of a name in **Search what changed**. Set **From** and **To** days if you want to narrow it. Use **Clear** to reset.",
      "Read the table: **When (IST)**, **Who**, **Action**, **What** and **Detail**. A settings change shows the old and new values. Secrets are shown as (hidden).",
      "Use **Older** and **Newer** to page. Each page has 50 rows.",
    ]],
    ["h", "Check nobody tampered with the log"],
    ["steps", [
      "Click **Check change log**.",
      "Wait for the answer. **Intact.** means every row links to the one before it. **Not intact.** shows the first broken row with its number, time, action and the problem.",
      "If it says the oldest rows were trimmed by the size limit, that is normal and is not tampering.",
    ]],
    ["h", "Download evidence"],
    ["ul", [
      "**Download CSV** saves the rows that match your filters.",
      "**Audit pack (zip)** needs a **From** or **To** day first. It holds emails, WhatsApp messages, LinkedIn events, the change log, consent records, login history and the hash-chain check, with a README. Only leads you can see are included.",
      "For one person, open their lead and click **Download audit pack** in the audit area of the lead. It covers only that lead.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The table lists rows for your filters. If it says **Nothing recorded for these filters.**, widen the dates or click **Clear**.",
      "Downloading a pack is itself recorded under **Audit packs**.",
    ]],
    ["warn", "A pack holds personal data about real people: names, emails, messages and logins. Store it safely, share it only with people who need it, and delete it when done."],
    ["tip", "People who run a client company's account see the changes in their own account under {{menu:Account activity}} instead."],
    ["tip", "To see who sent a message and why, open {{menu:Outbox & sending}}. Each row has a **Why it was sent** line and a label that says whether the app sent it by itself or a person did."],
  ],
};
