// Page chapter: Simple rules.
export default {
  id: "workflows", title: "Simple rules", group: "Menu pages", order: 200, kind: "page", masOnly: true, roles: ["owner", "caller", "viewer"], menu: "Simple rules",
  summary: "One-step \"when this happens, do that\" rules, such as sending a WhatsApp template after a yes.",
  blocks: [
    ["p", "{{menu:Simple rules}} holds [[Simple rule|simple rules]]: when something happens, do one thing. Each rule runs once per lead. WhatsApp actions still respect [[Consent|consent]]. For several steps, waits and branches, use the [Flow builder](#help/flows)."],
    ["h", "Who can use it"],
    ["ul", [
      "Only Mas Callnet staff logins see this page. Client company logins do not.",
      "Only an owner can add, switch on or off, or delete rules.",
      "Callers and viewers see the list with each rule marked **On** or **Off**.",
    ]],
    ["h", "Add a rule"],
    ["steps", [
      "In **New rule**, type a **Name**, for example \"Yes by email, then WhatsApp\".",
      "Under **When**, pick what starts it (see the table below), then pick the value.",
      "Under **Then**, pick what to do, then the value.",
      "Click **Add rule**. Or click **Add suggested rules** to get a ready set.",
    ]],
    ["table", { head: ["When", "Options"], rows: [
      ["They reply, and it sounds…", "interested, question, not now, wrong person, unsubscribe, other."],
      ["Their stage becomes…", "A stage such as verified, contacted, engaged, interested or customer."],
      ["In the product they…", "An activity such as opened the demo, drafted a role, published a role, screened a candidate, booked an interview or converted."],
      ["We emailed and heard nothing for (days)…", "A number of days."],
    ] }],
    ["table", { head: ["Then", "What it does"], rows: [
      ["Send WhatsApp template", "Sends an approved template, such as First message (after yes) or Nudge: demo not opened."],
      ["Create a task for me", "Adds a task. You can use the lead's company and first name in the text."],
      ["Move them to stage", "Changes the lead's stage (Not now, Engaged, Disqualified or Call done)."],
      ["Add a note to their history", "Writes a line on the lead's timeline."],
    ] }],
    ["h", "Manage your rules"],
    ["ul", [
      "The **Your rules** card lists each rule as \"when, then\".",
      "Owners tick **On** to switch a rule on or off, and click **Delete** to remove it.",
      "**Check time-based rules now** runs the \"heard nothing for days\" rules immediately instead of waiting.",
    ]],
    ["tip", "Start with **Add suggested rules**, read them, and switch off any you do not want."],
    ["p", "Related: [Flow builder](#help/flows), [Replies](#help/replies), [Sequences & templates](#help/templates)."],
  ],
};
