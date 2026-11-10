// Recipe: read the sales funnel for a month.
export default {
  id: "recipe-read-funnel", title: "Read the funnel for a month", group: "How-to recipes", order: 60, kind: "recipe",
  summary: "Pick a month, read the top tiles, the funnel, where deals are lost, how fast you respond, and why two pages may differ.",
  blocks: [
    ["h", "Before you start"],
    ["p", "Anyone who can open {{menu:Sales funnel}} can read it. Only owners and admins change the funnel settings at the bottom. Numbers refresh by themselves every minute while the page is open."],
    ["h", "Steps"],
    ["steps", [
      "Open {{menu:Sales funnel}}.",
      "Under **Quick ranges** click **This month**. Or pick **From** and **To** dates with the date pickers. You cannot pick a future date, and **From** cannot be after **To**.",
      "Read the strip under the filters. It says the range, for example \"1 Nov 2026 to 30 Nov 2026\", when the page last refreshed, and how many records were updated today. If you leave **From** and **To** empty it reads **As on** today.",
      "Read the tiles: **Inquiries**, **MQLs**, **SQLs**, **Opportunities (Meeting Held)**, **Won (from these leads)** and **Deal Value Closed (₹)**. Each shows the change against the previous period of the same length. Click a tile to see the leads behind the number.",
      "Under **Funnel health**, read the funnel bars. Each bar is every lead created in your range that reached that stage by the **To** date. The percentage is of the stage before. **Stage conversion** gives the same as counts and rates.",
      "Read **Funnel leakage: Closed Lost by the stage it was lost at**. Rows are the stage where the deal was lost; columns are the lost reasons. Click a number to list those leads.",
      "Under **Speed and discipline**: **Speed to lead** is the time from creation to first human contact (average and median). **SLA compliance** shows how many leads got a first response within the SLA hours. **Stalled leads** are open leads with no activity for more than the set number of days, biggest value first.",
      "Scroll to **Pipeline and forecast**, **Attribution and people** and **Closed revenue** for coverage, velocity, sources, owners and money won.",
      "To filter, open the lists for **Lead source**, **Industry**, **Service line** and **Lead owner** and tick values. **Reset** clears all filters.",
    ]],
    ["p", "Terms: [[MQL]], [[SQL]], [[SLA]], [[Cohort|cohort]], [[Speed to lead]], [[Stalled lead|stalled leads]]. Click any of them in the page for a short meaning."],
    ["h", "How to know it worked"],
    ["ul", [
      "The strip shows **Live**. If it says **Could not refresh just now; showing the last numbers.**, the page is showing older numbers. Reload.",
      "Clicking a tile opens a list that matches the number on the tile.",
    ]],
    ["h", "Why this page may not match another page"],
    ["ul", [
      "Counts here are for leads **created** in the range (a cohort). A lead created in October and won in November counts in October's funnel but in November's **Closed revenue**.",
      "**Closed revenue** is by the Closed Date, the day the deal was won, not the day the lead was created.",
      "Days start and end at midnight India time ([[IST]]). A late-evening record sits on the same India date, even if another system shows a different date.",
      "A stage with a back-filled date has no real date. The app filled it with the Created Date. See [update a lead through the sales stages](#help/recipe-update-stage).",
    ]],
    ["tip", "Owners and admins can change the first-response SLA hours, the stalled-after days and monthly targets under **Funnel settings** at the bottom. Changing them changes the numbers for all dates."],
  ],
};
