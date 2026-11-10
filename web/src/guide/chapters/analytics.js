// Analytics page: six tabs for one period.
export default {
  id: "analytics", title: "Analytics", group: "Menu pages", order: 230, kind: "page", menu: "Analytics",
  summary: "Six tabs of numbers for one period you pick: where every lead stands, an overview, the outreach funnel, channels, sources and your team.",
  blocks: [
    ["p", "Open {{menu:Analytics}} to see what is working. Pick a period once at the top and every tab uses it. All days and times are India Standard Time ([[IST]])."],
    ["img", "analytics", "The Analytics page with the period picker at the top and the Pipeline tab showing lead tiles and the lead funnel"],
    ["h", "Who can use it"],
    ["p", "Owners, admins, callers and viewers can all open Analytics. It only shows numbers and lets you open the leads behind them. Nobody changes data here."],
    ["h", "Pick the period"],
    ["steps", [
      "In the **Period** box, click **Last 7 days**, **Last 30 days** or **Last 90 days**.",
      "Or click **Custom**, choose the two dates, then click **Apply**. You can pick at most 731 days (about two years). **Apply** stays grey if the dates are wrong.",
      "The small line under the box shows the exact dates and the earlier period of the same length that the page compares with.",
    ]],
    ["p", "Your choice is remembered in this browser, and the Reports page uses the same one."],
    ["h", "The six tabs"],
    ["table", { head: ["Tab", "What it shows"], rows: [
      ["Pipeline", "Where every lead stands right now. Tiles for All leads, Have phone / email / LinkedIn, Enriched, Attempted, Connected, Interested, Not interested, Asked for follow-up and Wrong / inactive number. Below: the lead funnel, what connected leads said, a daily trend, call outcomes, channels tried, leads by stage, and tables By owner and By source."],
      ["Overview", "Ten tiles: New leads, Contacted, Emails sent, Open rate, Replied, Reply rate, Interested, Meetings, Customers and Unsubscribed. Each shows the change against the earlier period."],
      ["Funnel", "Leads first contacted in the period and how far they have got: Contacted, Replied, Interested, Meeting, Customer. Also the median days between steps, and the same funnel grouped by campaign, sequence step, template variant or lead source."],
      ["Channels", "Email and LinkedIn results, the best day and hour for replies, which job roles respond, companies that engage, interested leads going cold, team activity, data quality and the change log of manual corrections."],
      ["Sources", "Results by lead source and by sector, cost per lead and per customer, and the top leads right now."],
      ["Team", "One row per person: total actions, stage moves, calls, notes and other actions."],
    ] }],
    ["h", "See the leads behind a number"],
    ["steps", [
      "On the **Pipeline** tab, click a tile, a bar in **Lead funnel**, a slice of the outcomes chart, or a number in the **By owner** or **By source** tables.",
      "A list opens below with company, name, phone, email, stage and owner. It shows the newest leads first and tells you if more exist.",
      "Click a company name to open the lead. Click **Close** to hide the list.",
    ]],
    ["h", "Pipeline tab options"],
    ["ul", [
      "**All leads (now)** counts every lead as it stands today. **Only leads added in this period** counts only leads created inside the period.",
      "In the funnel, the percentage on each bar is of all leads for Enriched and Attempted, of attempted leads for Connected, and of connected leads for Interested.",
    ]],
    ["h", "How to read the Funnel tab"],
    ["ul", [
      "Rates are of the leads contacted in the period. A lead counts once per step.",
      "**Stage velocity** shows the median days between steps. The median is the middle value, so one very slow lead does not distort it.",
      "Use **Group by** to compare campaigns, sequence steps, template variants or lead sources. For steps and variants, each lead counts under the email that got its reply, or its latest email.",
    ]],
    ["tip", "This Funnel tab is about outreach. For deals, stages like Proposal and Closed Won, and revenue, use the [Sales funnel](#help/funnel) page."],
    ["warn", "Opens on the Overview tab are counted only for leads emailed in the period, so a lead emailed last month does not add opens here, even if it opens the email today."],
    ["h", "Related"],
    ["ul", [
      "[Reports](#help/reports) for what each person did each day.",
      "[Sales funnel](#help/funnel) for deal stages and revenue.",
      "[Glossary](#help/glossary) for short words such as [[KPI]].",
    ]],
  ],
};
