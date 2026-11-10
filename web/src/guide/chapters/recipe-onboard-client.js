// Recipe: onboard a new client company.
export default {
  id: "recipe-onboard-client", title: "Onboard a new client company", group: "How-to recipes", order: 70, kind: "recipe",
  roles: ["owner"], masOnly: true,
  summary: "Create the client's account, workspace and logins, give it its own sending, set limits, and check it works.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only Mas Callnet owners can do this. Some buttons (Details, approving a sign-up, Stop all sending) also need super admin rights. If there is no super admin named, every owner counts as one.",
      "Have ready: the company name, the first person's name and email, their role, and a starting password of at least 10 characters.",
      "If the client will send email under their own name, also have their sending email address, sender name and email provider API key (Resend).",
    ]],
    ["p", "A client company is an [[Account|account]]. An account holds [[Workspace|workspaces]] (one per brand or team) and logins. A client's logins see only their own account."],
    ["h", "Path A: you create the account"],
    ["steps", [
      "Open {{menu:Accounts}} in Setup. Click **New account**.",
      "Type the **Company name**. Leave **Status** as **Active** (it can be **Pending** or **Suspended**). Click **Create account**.",
      "Find the new row and click **Manage**. A window opens.",
      "Under **Workspaces and logins**, type a **Workspace name** (usually the client's name) and click **Add workspace**.",
      "On the next line enter the person's **Email**, **Name**, a role (**Workspace admin**, **Caller** or **Viewer**), the workspace, and a **Password (10+ characters)**. Click **Add login**. Give the password to them in person or by phone. The first login should be a workspace admin: they become the account admin.",
      "Under **Limits (blank = unlimited)** set what the client may use: **Logins**, **Workspaces**, **Emails a day**, **Imports a day**, **Lookup credits**, **AI credits** and the LinkedIn maximums. Click **Save account**.",
      "Under **Platform integrations this account may use**, tick any Mas Callnet service you want the client to share (for example the sending domain). Click **Save account**.",
      "Under **Sending identity**, either fill the client's own **Sender name**, **Company in the footer**, **Postal address**, **From email (own domain)**, **From name** and **Resend API key**, or rely on a grant from the step above. Click **Save sending settings**. Leave **Send for real** unticked until you have practised.",
    ]],
    ["h", "Path B: the client signs up themselves"],
    ["steps", [
      "Public sign-up must be open. In {{menu:Accounts}} look for **Public sign-up** and use **Open sign-up** or **Close sign-up**.",
      "The client confirms their email by a link that works for 24 hours. Their account is made with the status **Pending** and its first login is a workspace admin. They see **Waiting for approval** and cannot import or send.",
      "You see the account under **Waiting for approval**. Click **Details** to read it, then **Approve**, **Ask for details** (emails them a question) or **Reject** (emails them the reason).",
      "After approval, approve their first workspace if it is waiting (under **Workspace requests** in **Details**, or in {{menu:Workspaces}}), then set limits and sending as in Path A.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The account row shows the status **active**, one workspace and the login count.",
      "The **Sending** column reads **Own domain** (it has its own keys), **Granted** (it uses Mas Callnet's), or **Held** (it cannot send yet). In **Manage**, the line under **Sending identity** says **Emails can go out for this account.** or why not.",
      "Ask the client admin to sign in. They should see only their own workspace. In **Details**, use **View as** next to a login to see exactly what they see (read-only for 30 minutes).",
      "Under **Usage against limits** in **Details** you can watch usage.",
    ]],
    ["warn", "Do not tick **Send for real** for a client until they have practised and their sending domain and footer are right. **Delete account** erases all its leads, messages, workspaces and logins for good. Use **Export data (zip)** first. See also [suspend an account](#help/recipe-suspend-account)."],
    ["tip", "Limits stop work with a plain message, and warn the account admins and super admins at 80% and 100%. See [Setup: Accounts](#help/setup-accounts) and [Setup: Usage & limits](#help/setup-usage)."],
  ],
};
