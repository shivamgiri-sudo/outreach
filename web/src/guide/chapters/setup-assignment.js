export default {
  id: "setup-assignment", title: "Assignment", group: "Setup", order: 25, kind: "setup", menu: "Assignment", roles: ["owner", "admin"],
  summary: "Decide who owns each new lead, how fast they must act, and whether people see only their own leads.",
  blocks: [
    ["p", "Open {{menu:Assignment}} in {{menu:Setup}}. Every lead has one owner. A person sends email, LinkedIn and WhatsApp only to leads they own."],
    ["h", "Who can use it"],
    ["p", "Owners and workspace admins."],
    ["h", "Teams and rules"],
    ["ul", [
      "**Teams** are groups of people. New leads rotate through a team in turn. The **default team** gets leads that match no rule.",
      "**Rules** are checked from the top. The first rule whose filled boxes all match sends the lead to its team. Use Up and Down to change the order.",
    ]],
    ["h", "First touch"],
    ["p", "The clock runs in working hours. After the alert time the owner is told. After the move time the lead goes to the next person in the team and the manager is told."],
    ["h", "Ownership mode"],
    ["steps", [
      "Read the report: lists given out, lists in the admin pool, leads with no owner, and leads whose owner differs from their list.",
      "Click **Fix owners** for the leads you agree with. Nothing changes until you do.",
      "Turn **ownership mode** on. If some leads have no owner, tick the box that says you understand.",
    ]],
    ["warn", "Once ownership mode is on, a member sees only their own leads and lists. Review the report with the team first."],
    ["h", "Related"],
    ["ul", ["[Lists](#help/lists)", "[Leads](#help/leads)"]],
  ],
};
