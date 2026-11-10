// Recipe: suspend (and reactivate) a client account.
export default {
  id: "recipe-suspend-account", title: "Suspend or reactivate an account", group: "How-to recipes", order: 150, kind: "recipe",
  roles: ["owner"], masOnly: true,
  summary: "Stop a client account at once: logins signed out, sending held. Bring it back with Reactivate.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only a Mas Callnet super admin can do this. The **Details** button, which holds Suspend, appears for super admins only.",
      "Decide first whether you want to suspend one account or stop all sending for every account. This recipe is for one account. For everyone, use **Stop all sending** at the top of {{menu:Accounts}}.",
    ]],
    ["h", "Suspend"],
    ["steps", [
      "Open {{menu:Accounts}} in Setup.",
      "Find the account and click **Details**.",
      "Click **Suspend**. A window asks for a **Reason (optional, for the change log)**. Type one and click **Suspend**.",
    ]],
    ["p", "The message is: the account is suspended, everyone is signed out and sending is held. The app also deletes the account's saved tokens for connected tools. Suspended accounts cannot sign in or send."],
    ["h", "Reactivate"],
    ["steps", [
      "Open {{menu:Accounts}}, find the account (use the status filter to show suspended ones) and click **Details**.",
      "Click **Reactivate**. People can sign in again and sending follows the account's own settings.",
      "If a **Release the hold** button shows, the account was held automatically because too many emails bounced or drew spam complaints. Check the list first, then click it.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The status in the account's row reads **suspended** (or **active** after Reactivate).",
      "A suspended account's **Sending** column reads **Held**.",
      "The action is recorded in the change log with your reason. See [read the audit trail](#help/recipe-audit-trail).",
    ]],
    ["warn", "Suspending signs everyone in that account out at once, including people in the middle of work. Warn the client's admin first if you can. If you only want to pause emails, tell the client's owner to switch **Send for real** off instead."],
  ],
};
