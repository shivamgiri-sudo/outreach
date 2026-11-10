// Topic chapter: the 18 parts of the Sales funnel page, in the order they appear on screen.
export default {
  id: "funnel-parts", title: "The 18 parts of the Sales funnel", group: "Menu pages", order: 250, kind: "topic",
  summary: "Every tile, chart and table on the Sales funnel page: what it counts and how the number is worked out.",
  blocks: [
    ["p", "This chapter goes down the [Sales funnel](#help/funnel) page from top to bottom. Remember the rule: stage counts are for leads **created** in your date range and reaching a stage by the To date; revenue follows the **Closed Date**. Numbers shown as links open the leads behind them."],
    ["p", "Money is shown in rupees. Big amounts are shortened on tiles, for example 12.5 L for lakhs and 1.2 Cr for crores. Tables show the full amount."],
    ["h", "At the top"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["1. Status strip", "A dot with **Live** (or a red dot if a refresh failed), **Last refreshed** time, how many records were updated today, and the date range in words."],
      ["2. Six snapshot tiles", "**Inquiries**, **MQLs**, **SQLs**, **Opportunities (Meeting Held)**, **Won (from these leads)** and **Deal Value Closed (₹)**. Each shows the change against the earlier period (an up or down arrow with a percentage) and the earlier number after the word before."],
    ] }],
    ["tip", "A dash instead of a percentage means there was nothing in the earlier period to compare with."],
    ["h", "Funnel health"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["3. Funnel", "A bar for each of the eight stages. Each bar counts every lead that reached that stage by the To date. The percentage is of the stage before. Click a bar for its leads."],
      ["4. Stage conversion", "A table with the count and rate from each stage to the next, and a last row for Inquiry to Closed Won overall."],
      ["5. Funnel leakage", "Where you lose deals. Rows are the stage a lead was lost at, columns are the lost reasons (Price, No Budget, Chose Competitor, No Response, Not ICP, Timing, Other). Click a count to see those leads. The top right shows the total lost. A reason shown as Not given means nobody recorded it."],
    ] }],
    ["h", "Speed and discipline"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["6. [[Speed to lead|Speed to lead]]", "Average and median hours from the day a lead was created to the first contact by a person. Below, the same by owner. Only leads with a first contact are measured, and the card says how many."],
      ["7. [[SLA]] compliance", "A dial with the share of measured leads first contacted within your First-response SLA hours (24 by default), and the count, such as 18 of 25."],
      ["8. Stalled leads", "Open leads with no activity for more than your Stalled after days (7 by default), biggest value first. Each row has the company, owner, stage, value and days idle. Click the **stalled** count for the full list. [Read more](#help/glossary/stalled-lead)."],
    ] }],
    ["h", "Pipeline and forecast"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["9. Pipeline value by stage", "For open leads, two bars per stage from Inquiry to Negotiation: the total estimated monthly value and the weighted value, which is the value multiplied by that stage's chance of winning. Below the chart: open total and weighted total. Recycled leads are left out."],
      ["10. Pipeline coverage", "Open pipeline divided by the targets set for the months in your range, shown as a multiple such as 3.2x. It also shows the open pipeline and the target. A dash means no target is set; add one in **Funnel settings**."],
      ["11. Sales velocity", "Expected revenue per day. It uses open opportunities (leads at Meeting or later), the win rate, the average deal value per month and the average sales cycle. Win rate is the share of leads closed in the range that were won rather than lost."],
      ["12. Average days per stage", "How many days leads usually spend between one stage and the next, plus a last row for Inquiry to Won in total, with how many leads were measured. Back-filled stages are left out."],
    ] }],
    ["h", "Attribution and people"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["13. Source-wise funnel", "One row per lead source with a count at each stage, the rate to SQL, the rate to Won and the Won value. Click a source name to see its leads."],
      ["14. Lead score grid", "Open leads in a 3 by 3 grid: Fit (A, B, C) down the side and engagement (Hot, Warm, Cold) across the top. Darker cells have more leads. Click a number for its leads. Leads with no fit or engagement recorded are not counted; these usually come from the Lead Score (Fit) and Engagement columns of an imported sheet."],
      ["15. Recycled and nurture pool", "Leads parked in Recycled / Nurture, grouped by how long they have waited (0-30, 31-90, 91-180 and 180+ days), with the reasons recorded. Click the leads count for the list."],
      ["16. Owner leaderboard", "One row per lead owner, best first by Won value then deals won. Columns: Leads, SQLs, Meetings, Proposals, Won, Win %, Won value and Avg response (hours to first contact). Click an owner for their leads."],
    ] }],
    ["h", "Closed revenue"],
    ["table", { head: ["Part", "What it shows"], rows: [
      ["17. Closed revenue tiles and months", "Four tiles: **Monthly Value Closed (₹)**, **Total Contract Value Closed (₹)** ([[TCV]]), **Deals Closed** and **Average Deal Size (₹)**, each against the earlier period. Under them, **Closed monthly value by month** is a column chart by the month the deals closed."],
      ["18. Breakdowns", "Four tables: **By source**, **By industry**, **By service** and **By owner**. Each shows Deals, Estimated, Actual and Variance. Estimated is what the deal was expected to be worth when it was in the pipeline, Actual is the closed value. Variance is the gap as a percentage, positive if you closed above the estimate and negative if below. Leads with no value in that field appear under Not set."],
    ] }],
    ["h", "Common questions"],
    ["ul", [
      "**Why is a stage count lower than I expect?** Only leads created in your date range are counted. Widen the range, or clear both dates.",
      "**Why does revenue not match the funnel?** Revenue uses the Closed Date. A deal can be created last year and closed this month.",
      "**Why are some averages blank?** There were no leads to measure in that range, or the stages were back-filled.",
    ]],
    ["p", "To change a deal's stage and the rules around it, see [Update stage](#help/funnel/update-stage)."],
  ],
};
