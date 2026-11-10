// Page chapter: Intelligence (older page, now the Channels tab of Analytics).
export default {
  id: "intelligence", title: "Intelligence", group: "Menu pages", order: 280, kind: "page", masOnly: true, roles: ["owner", "caller", "viewer"], menu: "Intelligence",
  summary: "An older page on channel results, reply timing, going-cold leads, data quality and the change log. The main view is Analytics.",
  blocks: [
    ["p", "{{menu:Intelligence}} is an older page, still reachable with Ctrl K (or Cmd K) quick search. The channel numbers now live on the **Channels** tab of {{menu:Analytics}}, which has a period picker. Use [Analytics](#help/analytics) for day-to-day work."],
    ["h", "Who can use it"],
    ["p", "Mas Callnet logins only. Owners and callers can create follow-up tasks; only an owner can download the change log."],
    ["h", "What is on it"],
    ["ul", [
      "**Email** and **LinkedIn** tiles: leads emailed, opened, replied; LinkedIn started, requests sent, accepted, replied, and Open Profile people.",
      "**When people reply**: best day, best hour in [[IST]] and usual time to reply, with bars per weekday.",
      "**Which roles respond**: leads, contacted, replied and interested for each kind of role.",
      "**Connection note test**: requests sent, accepted and accept rate for note A and note B. Wait for 30 or more requests per note before trusting the gap.",
      "**Companies that engage**: the companies with the most replies and interest.",
      "**Going cold**: interested leads with no activity for some days. Click **Create follow-up tasks for all** to give each one a task. Leads that already have one are skipped.",
      "**Team activity**: actions with a person's name on them.",
      "**Data quality**: counts of leads with no email, no phone, no LinkedIn link, generic addresses like info@, or a failed mailbox check.",
    ]],
    ["h", "Change log"],
    ["p", "A list of every manual correction to a lead: when, who, which lead and field, what it was, what it became and why. Owners can click **Download CSV**. This is useful for checking who changed what."],
    ["p", "Related: [Insights](#help/insights), [Analytics](#help/analytics), [LinkedIn outreach](#help/linkedin)."],
  ],
};
