// Page chapter: Call log.
export default {
  id: "calllog", title: "Call log", group: "Menu pages", order: 121, kind: "page", menu: "Call log",
  summary: "Every call attempt across all leads: who made it, on which lead, when and how it ended.",
  blocks: [
    ["p", "{{menu:Call log}} lists every call attempt on every lead you can see, newest first, with a summary per caller. Use it to check what your team did today and how calls ended. For one lead's full history open the lead and use its **Activity** tab."],
    ["h", "Who can use it"],
    ["ul", [
      "Everyone who can sign in can open the page and see the calls on leads they may see.",
      "With lead ownership on, a member sees calls on their own leads only and a viewer sees their team's.",
      "Only an owner can download the CSV.",
    ]],
    ["h", "Find calls"],
    ["steps", [
      "Open {{menu:Call log}}. The dates start at today ([[IST]]).",
      "Pick a **From** and **To** date, a caller, an outcome or a channel, or type in the search box.",
      "Read the **per caller** table above the list: calls, connected, unanswered, interested, meetings and connect rate. It ignores the caller and outcome filters so you can compare callers.",
      "Click a row to open the lead.",
    ]],
    ["h", "Lead quality"],
    ["p", "Above the call table, **Lead quality** shows how many leads callers marked **Invalid** or **Consultant**, as a percentage per data source, with the reasons given and who marked them. A source with a high invalid rate is a bad source: raise it with whoever supplied the data. Marked leads have their own **Invalid lead** and **Consultant lead** choices in the Leads page's contact status filter."],
    ["tip", "Owners can press **Download CSV** to get the filtered list as a CSV that opens in Excel. Each download is written to the audit log."],
    ["p", "Related: [Call desk](#help/calls), [Leads](#help/leads)."],
  ],
};
