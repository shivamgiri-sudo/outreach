// Page chapter: Insights (older page, now the Sources tab of Analytics).
export default {
  id: "insights", title: "Insights", group: "Menu pages", order: 270, kind: "page", masOnly: true, roles: ["owner", "caller", "viewer"], menu: "Insights",
  summary: "An older summary of what works by lead source and sector. The same numbers are on the Sources tab of Analytics.",
  blocks: [
    ["p", "{{menu:Insights}} is an older page. You can still reach it by pressing Ctrl K (or Cmd K) and typing its name. The main place for these numbers is the **Sources** tab of {{menu:Analytics}}, which adds a period picker. See [Analytics](#help/analytics)."],
    ["h", "Who can use it"],
    ["p", "Mas Callnet logins only. Client company logins and workspace admins do not see it. Only an owner can type the monthly spend."],
    ["h", "What is on it"],
    ["ul", [
      "Tiles: Leads, Emails sent, Reply rate, Interested or further, Unsubscribe rate, WhatsApp sent, Customers, and Hot / warm / cold. Rates are of leads that were emailed.",
      "**By lead source**: leads, replied, interested, customers, cost per lead and cost per customer.",
      "**Stage by source**: how many leads from each source sit in each stage.",
      "**By sector**: leads per sector as bars.",
      "**Top leads right now**: company, score and stage.",
    ]],
    ["h", "Fill in your spend (owners)"],
    ["steps", [
      "Open **Cost per lead and per customer** at the bottom.",
      "Type the monthly spend in rupees for each source.",
      "Click **Save spend**. Cost per lead is the spend divided by the leads that source brought in over the last 30 days. Cost per customer divides by those who became customers.",
    ]],
    ["p", "**Download insights** saves every number as one table. Related: [Intelligence](#help/intelligence), [Analytics](#help/analytics), [Reports](#help/reports)."],
  ],
};
