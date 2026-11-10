export default {
  id: "setup-scoring", title: "Lead scoring", group: "Setup", order: 40, kind: "setup", menu: "Lead scoring", roles: ["owner"], masOnly: true,
  summary: "Choose how many points each buying signal is worth so the best leads rise to the top.",
  blocks: [
    ["p", "Open {{menu:Lead scoring}} in {{menu:Setup}}. Every lead gets a score from 0 to 100. This section sets how the score is worked out and where **warm** and **hot** begin."],
    ["h", "Who can use it"],
    ["p", "Owners only."],
    ["h", "The five signals"],
    ["table", { head: ["Box", "What earns points", "Default"], rows: [
      ["Open roles (log scale)", "How many open roles the company has. More roles score higher, but on a log scale: 80 roles beat 10, with no upper company-size limit.", "30"],
      ["Hard-to-fill roles (old / reposted)", "Roles that have been open a long time or posted again.", "25"],
      ["Size and India fit", "Company size and whether it is in India.", "15"],
      ["Opens, clicks, replies", "How much the lead has engaged with your messages.", "20"],
      ["Said yes to contact", "The lead agreed to be contacted.", "10"],
    ] }],
    ["p", "The page shows the total of the five boxes. The defaults add up to 100. If your total is above 100, scores are capped at 100. At least one box must be above 0."],
    ["h", "Other numbers"],
    ["ul", [
      "**Warm from** (default 40) and **Hot from** (default 70): the scores where a lead is called warm and hot. Warm must be lower than hot.",
      "**Open roles for full marks** (default 50): how many open roles earn the full points for that signal. The label uses your own word for open roles if you renamed it. The lowest allowed value is 2.",
      "**Smallest company size that fits** (default 20): companies at or above this size count as a size fit.",
    ]],
    ["steps", [
      "Change the numbers you want. Each weight is a whole number from 0 to 100.",
      "Click **Save scoring**. A message says every lead is scored with the new numbers now.",
      "Click **Back to defaults** to restore the original numbers (this saves right away).",
    ]],
    ["warn", "Saving new weights changes every lead's score at once, and lists sorted by score will reorder. Change one thing at a time so you can see what it did."],
    ["h", "Related"],
    ["ul", ["[Leads](#help/leads)", "[Hiring engine](#help/engine)", "[Glossary](#help/glossary)"]],
  ],
};
