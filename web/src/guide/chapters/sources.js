export default {
  id: "sources", title: "Data sources", group: "Menu pages", order: 290, kind: "page", menu: "Data sources",
  // Workspace admins and client logins cannot open this page (web/src/App.jsx LIMITED_PAGES), so its chapter is hidden from them.
  roles: ["owner", "caller", "viewer"], masOnly: true,
  summary: "Test several contact-finding services on the same leads, see which finds the most emails and phones for the least cost, and keep the best.",
  blocks: [
    ["p", "{{menu:Data sources}} helps you choose between services that find emails and phone numbers. Instead of guessing, you give each service the same leads and compare the results side by side. Only public business contacts are used, and every value keeps a note of where it came from."],
    ["h", "Who can use it"],
    ["ul", [
      "Owners can run comparisons, use the results and import scraper files.",
      "Callers and viewers who are not limited to particular workspaces can read the providers and past results only.",
      "Admins and logins limited to a workspace do not see this page in the menu.",
    ]],
    ["h", "Providers"],
    ["p", "The **Providers** card lists every service. A green **Connected** tag means its key is set up. **Not set up** means it will be skipped. For a missing one, click **How do I get this?** for step-by-step help, or **Set up in Setup** to go to {{menu:Connections}}. Free providers show their cost note."],
    ["h", "Run a comparison (owners)"],
    ["steps", [
      "In the **Compare** card, tick the providers to test. Connected ones are ticked for you.",
      "Under **How many leads to test**, type a number from 1 to 100 (25 is the start).",
      "Click **Run comparison**. It can take a few minutes. Nothing is saved to your leads yet.",
      "Read the **Results** table.",
    ]],
    ["table", { head: ["Column", "Meaning"], rows: [
      ["Provider", "The service. A red note says if it stopped early and why."],
      ["Checked", "How many leads it looked up."],
      ["Found email, Found phone", "How many it found."],
      ["Coverage %", "The share of leads where it found something. Higher is better."],
      ["Credits, Cost per contact", "What it used. Free services say Free."],
      ["Bounced", "Emails to its addresses that bounced. This grows once sending starts, so check again later."],
      ["Errors", "Failed lookups. Hover for the messages."],
    ] }],
    ["steps", [
      "Pick the winner and click **Use these results** on its row.",
      "It only fills empty emails and phones, and skips unsubscribed or disqualified leads. The row then says **Used** and shows how many emails and phones were added.",
    ]],
    ["tip", "Run the test again in a few weeks. The **Bounced** column becomes much more useful after you have sent real emails."],
    ["h", "Past comparisons"],
    ["p", "Every run is kept with its date, number of leads and each provider's coverage. Click **Show** to look at an old one."],
    ["h", "Import from the website contact scraper (owners)"],
    ["p", "If you run the open-source contact scraper on your own computer (homepage or key pages only), upload its **JSON file** or paste the JSON, then click **Import**. The result says how many leads matched and how many emails and phones were added, and lists website names that had no lead."],
    ["p", "Related: [Connections](#help/setup-connections), [Hiring engine](#help/engine), [Importing leads](#help/leads-import)."],
  ],
};
