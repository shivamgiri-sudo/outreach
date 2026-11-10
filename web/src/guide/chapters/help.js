// Chapter for the Help centre page itself. This file is the pattern for every chapter: one default export, small blocks.
export default {
  id: "help", title: "Help centre", group: "Menu pages", order: 350, kind: "page", menu: "Help centre",
  summary: "This guide, the glossary of short words, and the step-by-step guides for connecting other services.",
  blocks: [
    ["p", "Open {{menu:Help centre}} from the menu. It has three tabs."],
    ["table", { head: ["Tab", "What it is for"], rows: [
      ["User guide", "How to use every page, with step-by-step recipes. You only see the chapters your login can use."],
      ["Glossary", "Every short word and abbreviation, such as [[MQL]] or [[SLA]], A to Z with a search box."],
      ["Setup guides", "Plain steps for getting a key, a password or a connection from another service, such as Gmail or WhatsApp."],
    ] }],
    ["h", "Find something fast"],
    ["steps", [
      "Type a word in the search box at the top left of the User guide tab. It searches the guide and the glossary together.",
      "Click a result to open it.",
      "On a phone, tap **Contents** to open the list of chapters.",
    ]],
    ["h", "Share or print a page"],
    ["ul", [
      "Move over any heading and press **Link** to copy a link straight to it. Anyone with a login can open it.",
      "Press **Print / save as PDF**. Tick **Include every chapter** to print the whole guide you can see.",
    ]],
    ["tip", "Short words with a dotted line under them show their meaning when you hover, tap or tab to them. Press Esc to close the box."],
  ],
};
