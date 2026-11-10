export default {
  id: "signals", title: "Buying signals", group: "Menu pages", order: 300, kind: "page", menu: "Buying signals",
  // Workspace admins and client logins cannot open this page (web/src/App.jsx LIMITED_PAGES), so its chapter is hidden from them.
  roles: ["owner", "caller", "viewer"], masOnly: true,
  summary: "Watch keywords, competitors and companies for fresh public news such as hiring drives or new centres, and turn the good ones into leads.",
  blocks: [
    ["p", "A [[Buying signal|buying signal]] is a sign that a company may need what you sell. {{menu:Buying signals}} reads public news for words you choose. You decide which company becomes a lead. It only reads the public news feed, never LinkedIn."],
    ["h", "Who can use it"],
    ["ul", [
      "Owners add and remove watches and press **Check now**.",
      "Owners, admins and callers can click **Add as lead** or **Dismiss** on an article. Viewers can only read.",
      "The page is in the menu for owners and for callers and viewers who are not limited to particular workspaces.",
    ]],
    ["h", "Set up what to watch (owners)"],
    ["steps", [
      "Choose the kind: **Keyword**, **Competitor** or **Company**.",
      "Type the words, up to 80 letters. For example \"BPO hiring Noida\", a competitor's name, or a company you want to track.",
      "Click **Watch** (or press Enter).",
      "Click **Check now** to look for news straight away. A message says how many new articles were found and whether any feed failed.",
    ]],
    ["p", "Your watches show as chips. Click the **x** on a chip to stop watching it. Under the card, \"Last checked\" shows when the news was last read."],
    ["h", "Work through the articles"],
    ["steps", [
      "Use **Show** to pick **New**, **Made into leads**, **Dismissed** or **All**.",
      "Read the headline (it opens the news site in a new tab). Under it you see the source, the date and which watch found it.",
      "For a useful one, check the **Company this is about** box (it is filled in when possible) and click **Add as lead**. If the company is already a lead, the article is added to its history instead.",
      "For a useless one, click **Dismiss**.",
    ]],
    ["tip", "Keep watches specific. \"hiring\" alone brings too much. \"call centre hiring Pune\" brings better matches."],
    ["p", "Related: [Leads](#help/leads), [Hiring engine](#help/engine), [Prospect search](#help/prospects) (it can also filter by signals)."],
  ],
};
