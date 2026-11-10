// Recipe: update a lead through the sales stages.
export default {
  id: "recipe-update-stage", title: "Update a lead through the sales stages", group: "How-to recipes", order: 50, kind: "recipe",
  roles: ["owner", "admin", "caller"],
  summary: "Move a lead from first inquiry to Closed Won or Closed Lost, and what the app asks for at each step.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "You need a login that can make changes: owner, admin or caller. Viewers can see the stage but not change it.",
      "The sales stage is separate from the outreach stage (new, contacted and so on). Changing one does not change the other.",
      "Have the numbers ready: estimated monthly value, and for a win the closed monthly value and contract months.",
    ]],
    ["h", "The stages, in order"],
    ["p", "Inquiry / Raw Lead, Marketing Qualified Lead ([[MQL]]), Sales Accepted Lead ([[SAL]]), Sales Qualified Lead ([[SQL]]), Discovery Meeting Held, Proposal Submitted, Negotiation / Commercials, then Closed Won. A lead can also leave the line as Closed Lost or Recycled / Nurture."],
    ["h", "Steps"],
    ["steps", [
      "Open {{menu:Leads}} and click the lead to open it.",
      "Find the **Sales stage** panel. It shows the current stage, the chance of winning and, once won, the total contract value.",
      "Pick the new stage in **Status**.",
      "Fill the boxes the stage needs (see the table). A star beside a box means it is required.",
      "Click **Save sales stage**.",
      "Read the message under the form. **Saved. Sales stage: (stage).** means it worked. A red message tells you exactly what is missing; fix it and save again.",
    ]],
    ["h", "What each stage needs"],
    ["table", { head: ["To reach", "You must have", "If missing the app says"], rows: [
      ["Sales Qualified Lead (SQL) and every later stage up to Closed Won", "**Est. monthly value (₹)** more than 0", "Fill Est. Monthly Value (₹) first: it is required from SQL onwards."],
      ["Proposal Submitted", "A meeting date already recorded. Move the lead to **Discovery Meeting Held** first.", "Proposal Submitted needs a Meeting Held Date first (Proposal Meets Only rule)."],
      ["Closed Lost", "A **Lost reason**: Price, No Budget, Chose Competitor, No Response, Not ICP, Timing or Other", "Pick a Lost Reason first: it is required to mark a lead Closed Lost."],
      ["Closed Won", "**Closed monthly value (₹)** more than 0. **Contract tenure (months)** is optional; leave it empty and it counts as 12.", "Fill Closed Monthly Value (₹) (more than 0) before marking Closed Won."],
    ] }],
    ["h", "What the app fills in for you"],
    ["ul", [
      "It stamps today's date on the new stage. If you skip stages, it fills the skipped ones with the same time and lists them under **Back-filled stages (no real date)**. Those dates are not real, so the speed numbers on the funnel are less exact for that lead.",
      "It never changes a stage date that is already set.",
      "For Closed Lost it records the stage where the deal was lost. For Closed Won it works out the total contract value as the closed monthly value times the months.",
      "Every change is added to **History** at the bottom of the panel, with who did it.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The coloured stage label beside **Sales stage** shows the new stage.",
      "**History** has a new line, \"(old stage) to (new stage) by (your name)\".",
      "The lead is counted in the right bar of {{menu:Sales funnel}}. See [read the funnel for a month](#help/recipe-read-funnel).",
    ]],
    ["warn", "A deal won by mistake counts toward revenue straight away. Check the closed monthly value and the months before you save **Closed Won**. A lead that was lost can still be moved to Closed Won later; its Closed Date then becomes the win date."],
    ["tip", "Importing from a sheet with a Lead Status column follows the same rules, and lists any status it could not apply. See [import a sheet or Excel file](#help/recipe-import-sheet)."],
  ],
};
