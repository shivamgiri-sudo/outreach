export default {
  id: "setup-activity", title: "Account activity", group: "Setup", order: 100, kind: "setup", menu: "Account activity", roles: ["admin"], clientOnly: true,
  summary: "For client companies: a list of everything changed in your account, including what the platform team did.",
  blocks: [
    ["p", "Open {{menu:Account activity}} in {{menu:Setup}} to see a record of changes in your company's account. It shows what happened, who did it and when, so nothing about your account changes without you being able to see it."],
    ["h", "Who can use it"],
    ["p", "Account admins of a client company. Callers and viewers do not see it. Mas Callnet owners use the [Audit](#help/setup-audit) section instead."],
    ["h", "What you see"],
    ["table", { head: ["Column", "Meaning"], rows: [
      ["When", "Date and time in India time."],
      ["Who", "The person or team that made the change."],
      ["What", "The kind of change."],
      ["Detail", "A short description of what was changed."],
    ] }],
    ["ul", [
      "It includes actions by the Mas Callnet platform team on your account, such as a change to limits or a suspension.",
      "It also includes every time a platform team member used **View as** to look at your account as one of your logins. That view is read-only and ends after 30 minutes.",
      "If the page says **Nothing yet**, no changes have been recorded.",
    ]],
    ["tip", "If you see a change you do not recognise, ask the person named in the **Who** column first. If it still looks wrong, tell your Mas Callnet contact."],
    ["h", "Related"],
    ["ul", ["[Usage & limits](#help/setup-usage)", "[Team members](#help/setup-members)", "[Security](#help/setup-security)"]],
  ],
};
