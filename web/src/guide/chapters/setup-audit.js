export default {
  id: "setup-audit", title: "Audit", group: "Setup", order: 90, kind: "setup", menu: "Audit", roles: ["owner"], masOnly: true,
  summary: "A tamper-evident record of who changed what and when, with a check button and a downloadable audit pack.",
  blocks: [
    ["p", "Open {{menu:Audit}} in {{menu:Setup}} to see who did what, and when. Times are in [[IST]]. Changes to settings show the old and the new value. Secret keys show as (hidden)."],
    ["img", "audit", "The Audit log table with filters, the Check change log button and the Download CSV and Audit pack buttons"],
    ["h", "Who can use it"],
    ["p", "Owners only. A client company's admins see their own account's record in [Account activity](#help/setup-activity) instead."],
    ["h", "Find an entry"],
    ["steps", [
      "Use **Action** to pick a group: Stage changes, Send for real, Bulk actions, Erasures, Logins and roles, Workspaces, LinkedIn agent, Settings saved, Contact corrections, Lists deleted, Campaigns deleted, Downloads (CSV/JSON), Backups and restores, Bulk deletes, Imports undone, AI-drafted messages or Audit packs.",
      "Use **Person** to see one team member's actions.",
      "Type in the search box to look for what changed.",
      "Pick a **From** and **To** day, or click **Clear**.",
    ]],
    ["p", "Columns: When (IST), Who (with their role), Action, What (the thing that changed) and Detail. The table shows 50 rows at a time; use **Newer** and **Older**."],
    ["h", "Check change log"],
    ["p", "Every row is linked to the one before it by a [hash chain](#help/glossary/hash-chain), like numbered pages sealed with wax. If someone edits or deletes a row, the chain breaks."],
    ["steps", [
      "Click **Check change log**.",
      "Read the box that appears. **Intact.** means nothing was altered. **Not intact.** names the first broken row, its time, action and the problem.",
    ]],
    ["tip", "A note about the oldest rows being trimmed by the size limit is normal. It is not a sign of tampering."],
    ["h", "Download"],
    ["ul", [
      "**Download CSV** saves the rows that match your filters as a [[CSV]] file.",
      "**Audit pack (zip)** bundles messages, the change log, consent and logins for a period. It stays greyed out until you pick a **From** and **To** day.",
    ]],
    ["warn", "The audit pack holds personal data about leads and staff. Keep it in a safe place and share it only with an auditor or a regulator who needs it. The download itself is recorded here as **Audit packs**."],
    ["h", "Related"],
    ["ul", ["[Read the audit trail](#help/recipe-audit-trail)", "[Export your data](#help/recipe-export-data)", "[Security](#help/setup-security)"]],
  ],
};
