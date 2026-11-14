// Page chapter: Leaderboard.
export default {
  id: "leaderboard", title: "Leaderboard", group: "Menu pages", order: 122, kind: "page", menu: "Leaderboard",
  summary: "Calls, connects, interested leads and meetings per caller, ranked, with progress against each person's daily target.",
  blocks: [
    ["p", "{{menu:Leaderboard}} ranks your callers for **Today**, **Yesterday**, **This week**, **This month** or a **Custom** range ([[IST]] dates). It adds up the same calls you see in [Call log](#help/calllog) and compares them with the daily targets people set on [My day](#help/myday)."],
    ["p", "For a **Custom** range, pick a **From** and a **To** date. The range runs from the start of the first day to the end of the last day in [[IST]], can be at most 92 days, and the From date cannot be after the To date. If the dates do not work, a short message under the date boxes says why. The change in brackets then compares with the same number of days just before the range."],
    ["h", "What each column means"],
    ["ul", [
      "**Calls**: call attempts, counted for the person who logged them. The number in brackets is the change against the same number of days just before.",
      "**Connected**: calls that reached a real conversation (connected, interested, meeting booked). A call-back request or a not-interested answer is a call that got through but is not counted as connected. The same rule is used on My day, the Call log and the daily report. **Connect rate** is connected divided by calls.",
      "**Interested** and **Meetings**: outcomes credited to the person who logged them.",
      "**Marked**: leads the caller marked **Invalid** or **Consultant**. A mark is a verdict on the lead, not a call, so it is kept out of Calls and Connect rate.",
      "**Target reached**: calls divided by the daily calls target times the working days so far (Monday to Saturday). A bar and the number are shown; \"No target\" means the target is zero.",
    ]],
    ["h", "Ranking"],
    ["p", "Use **Rank by** to rank by calls, connected, interested or meetings. People with the same number share a rank and are listed by name. Your own row is highlighted and the last row is the team total."],
    ["h", "Who sees what"],
    ["ul", [
      "Everyone who can open the page sees every caller's name, counts and target progress. A leaderboard is open by design.",
      "Nobody sees lead names, companies, phone numbers or notes here. Open the Call log for those.",
      "Only calls on leads you may see are counted. With lead ownership on, a caller's board covers the leads they can see, while owners and admins see their whole workspace.",
      "Viewers can read the page but cannot change targets.",
    ]],
    ["tip", "Targets are changed on My day: a person sets their own, an owner sets the team default or someone else's."],
    ["p", "Related: [Call log](#help/calllog), [My day](#help/myday), [Call desk](#help/calls)."],
  ],
};
