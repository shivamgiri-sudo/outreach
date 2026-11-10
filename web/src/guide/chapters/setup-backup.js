export default {
  id: "setup-backup", title: "Backup & restore", group: "Setup", order: 130, kind: "setup", menu: "Backup & restore", roles: ["owner"], masOnly: true,
  summary: "Download everything as one file, or replace the app's data from a backup file after seeing exactly what would change.",
  blocks: [
    ["p", "Open {{menu:Backup & restore}} in {{menu:Setup}}. It has two cards: **Download a backup** and **Restore from a backup**."],
    ["h", "Who can use it"],
    ["p", "Owners only. Restoring is limited further: only the super admin can restore."],
    ["h", "Download a backup"],
    ["p", "One file with everything: leads, messages, replies, tasks, campaigns and settings. Use it to move to your own server or to go back to today's state. For spreadsheets, use the download buttons on each page instead."],
    ["steps", [
      "Click **Download backup**. A file called outreach-backup.json.gz is saved.",
      "Keep it somewhere safe, such as an encrypted drive.",
    ]],
    ["p", "By default the file does not include connection keys, login password hashes or pairing tokens. After a restore you enter the keys again in {{menu:Connections}}. Team logins already on the server keep their passwords."],
    ["warn", "If you tick **Also include connection keys, login password hashes, tokens and secrets**, the file holds your API keys, mailbox passwords and the server secret in plain text. Anyone with it can send email and WhatsApp as you. Store it like a password. Never email it, chat it or put it in a shared folder, and delete it once you have moved."],
    ["h", "Restore from a backup"],
    ["p", "Restoring replaces everything in the app with the file. You first see what would change. Nothing happens until you confirm."],
    ["steps", [
      "Under **Backup file (.json.gz)**, choose the file.",
      "Click **Check this file**.",
      "Read the box. It names the backup date, whether keys are included, and a table with what you have **Now** and what you would have **After the restore**.",
      "If it is right, click **Replace everything with this backup**, then confirm **Replace all data**. Click **Cancel** to stop.",
      "A green **Restored.** message and notes appear. Real sending is switched off after a restore; switch it on again when you are sure.",
    ]],
    ["ul", [
      "The current data is kept as a copy on the server before it is replaced.",
      "Team logins (passwords and two-step login), the server secret and pairing tokens are never taken from a backup file. Logins that exist only in the backup are not restored; add them again in [Team members](#help/setup-members).",
      "Keys in a backup are used only when this server made and signed it.",
    ]],
    ["tip", "Download a backup before big changes, such as a large import, a bulk delete or a restore."],
    ["h", "Related"],
    ["ul", ["[Export your data](#help/recipe-export-data)", "[System health](#help/setup-health)", "[Connections](#help/setup-connections)"]],
  ],
};
