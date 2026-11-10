export default {
  id: "start-roles", title: "Roles and what each can do", group: "Start here", order: 30, kind: "start",
  summary: "The five kinds of login, what each one can open and change, and how workspaces limit what a person sees.",
  blocks: [
    ["p", "Every login has one role. The role decides which menu pages you see and whether you can change things. The role names on the **Add member** screen are Owner, Workspace admin, Caller and Viewer. Two more names are used in this guide: super admin and account admin."],
    ["table", { head: ["Role", "Who it is", "What it can do"], rows: [
      ["Super admin", "A few Mas Callnet people named on the server. Nobody can make themselves one inside the app.", "Everything an owner can do, plus the only right to add, change or remove owner logins. Owners manage client companies in {{menu:Accounts}}. Two-step login is required."],
      ["Owner", "Mas Callnet staff in charge of the portal.", "Everything: all data in every workspace, all settings, connections, exports, backup, flows and templates. Sees every menu page, including {{menu:Hiring engine}}."],
      ["Account admin", "The admin login of a client company (role Admin inside a client account).", "Manages its own company only: its workspaces, team members, security, LinkedIn agent, and the pages {{menu:Account activity}} and {{menu:Usage & limits}}. Cannot see other companies. A client login can never be an owner."],
      ["Workspace admin", "Admin role inside Mas Callnet. Always tied to one or more workspaces.", "Works leads like a caller and also adds and manages logins for its own workspaces, edits templates and snippets, and sets the funnel targets. No Connections, Accounts, Backup or other owner-only pages."],
      ["Caller", "Sales and calling staff.", "Works leads, calls, replies, meetings and tasks in the workspaces it was given. No settings, no connections, no CSV exports. An owner can also switch off editing of contact details for one caller."],
      ["Viewer", "Managers or clients who only look.", "Read only. Every change is refused, and a note at the top of each page says so."],
    ] }],
    ["h", "Who sees which menu pages"],
    ["ul", [
      "**Owners** see every page.",
      "**Logins limited to a workspace, and every admin**, see only the pages built from their own workspace's leads: {{menu:My day}}, {{menu:Dashboard}}, {{menu:Leads}}, {{menu:Companies}}, {{menu:Lists}}, {{menu:Analytics}}, {{menu:Sales funnel}}, {{menu:Replies}}, {{menu:Call desk}}, {{menu:Tasks & follow-ups}}, {{menu:Meetings}}, {{menu:Outbox & sending}}, {{menu:Sequences & templates}}, {{menu:Reports}}, {{menu:LinkedIn outreach}}, {{menu:Flow builder}}, {{menu:Setup}} and {{menu:Help centre}}.",
      "{{menu:Hiring engine}} is for owners only. Other pages show the message \"Only an owner can open this page.\"",
      "Inside {{menu:Setup}}, each role sees only its own sections. A caller or viewer sees only {{menu:My password & two-step}}. Admins also see {{menu:Workspaces}}, {{menu:Team members}} and {{menu:Security}}.",
    ]],
    ["h", "Workspaces and accounts"],
    ["p", "A **workspace** keeps one client's or one team's leads, lists and numbers apart from the others. An **account** is a whole client company and holds its workspaces and logins. When you add a member, you tick the workspaces that login may see. A login with no workspace sees nothing until you give it one."],
    ["p", "If you can see more than one workspace, a **Workspace** picker appears at the top of the menu. Owners with several accounts also get an **Account** picker. On a phone these pickers are in the top bar."],
    ["h", "Changing a role"],
    ["steps", [
      "Open {{menu:Team members}} in Setup.",
      "On the person's row open the manage menu and choose **Change role...**",
      "Pick the new role and save. Only the super admin can add, change or remove owner logins.",
    ]],
    ["tip", "Give people the lowest role that lets them do their job. A viewer cannot break anything; a caller cannot change settings."],
    ["p", "Related: [Team members](#help/setup-members), [Security](#help/setup-security), [Signing in](#help/start-signin). Words such as [Owner](#help/glossary/Owner) and [Workspace](#help/glossary/Workspace) are in the glossary."],
  ],
};
