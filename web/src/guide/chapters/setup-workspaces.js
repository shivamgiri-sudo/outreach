export default {
  id: "setup-workspaces", title: "Workspaces", group: "Setup", order: 60, kind: "setup", menu: "Workspaces", roles: ["owner", "admin"],
  summary: "A workspace keeps one client's leads, lists and numbers apart from every other client.",
  blocks: [
    ["p", "Open {{menu:Workspaces}} in {{menu:Setup}}. Each row is a workspace, usually one per client. A contact sits in one workspace only, so two clients never contact the same person from here."],
    ["h", "Who can use it"],
    ["table", { head: ["Role", "What they can do"], rows: [
      ["Owner", "Create, rename, approve or reject requests, manage members and delete."],
      ["Admin", "Rename and manage members of their own workspaces. In a client account, admins can send a request for a new workspace with **Ask for a workspace**; a platform owner approves it."],
      ["Caller, viewer", "No access to this section."],
    ] }],
    ["h", "The table"],
    ["p", "Columns: Workspace, Leads, Members and Created (who made it and when). Owners also see an Account column once a client account exists. The built-in one is marked **Default** and cannot be renamed or deleted. A workspace waiting for approval shows **Requested**."],
    ["h", "Create or request a workspace"],
    ["steps", [
      "Owners click **Create workspace**. Client admins click **Ask for a workspace**.",
      "Type the **Workspace name**, usually the client's name, up to 60 characters.",
      "Click **Create workspace** or **Send request**.",
      "For a request, an owner clicks **Approve** or **Reject** on the row.",
    ]],
    ["h", "Rename, members and delete"],
    ["ul", [
      "**Rename** changes the name people see in the workspace picker.",
      "**Members** lists logins limited to this workspace. Choose from **Add an existing login...** and click **Add**, or click **Take out** (then **Take out** again to confirm). Owners and logins with access to all workspaces always see it too. New people are added in [Team members](#help/setup-members).",
      "**Delete** (owners) asks what to do with the leads: move them to another workspace, or delete them permanently with their tasks and this workspace's lists. Type the workspace name to confirm.",
    ]],
    ["warn", "People limited to a deleted workspace lose access. Anyone left with no workspace sees nothing until you give them one."],
    ["h", "Working inside a workspace"],
    ["p", "Pick the workspace in the left menu. Imports go into it, and Leads, Lists and Intelligence show only its leads. To move single leads, select them on the {{menu:Leads}} page and choose **Move to workspace**."],
    ["h", "Related"],
    ["ul", ["[Team members](#help/setup-members)", "[Accounts](#help/setup-accounts)", "[Roles](#help/start-roles)"]],
  ],
};
