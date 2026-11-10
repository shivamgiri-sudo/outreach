// Page chapter: Replies.
export default {
  id: "replies", title: "Replies", group: "Menu pages", order: 110, kind: "page", menu: "Replies",
  summary: "One inbox for every reply from every channel, with a score that tells you which ones to answer first.",
  blocks: [
    ["p", "{{menu:Replies}} collects every reply that came back from email, LinkedIn, WhatsApp, SMS or a call. The portal reads each one and gives it a **label** (for example Interested or Not now) and a **score** from 0 to 100. A high score means the reply deserves a person's time now."],
    ["img", "replies", "The Replies page with four count tiles, filters and reply cards showing a label, a score and action buttons"],
    ["h", "Who can use it"],
    ["ul", [
      "Everyone can read replies and open the **Thread**.",
      "Owners, admins and callers can also reply, mark handled, snooze, assign and change the label.",
      "Viewers see the list only. The action buttons are hidden for them.",
    ]],
    ["h", "The four tiles at the top"],
    ["table", { head: ["Tile", "What it counts"], rows: [
      ["need a person", "Replies that still need someone to act."],
      ["unread", "Replies nobody has opened yet. Unread cards have a coloured bar on the left."],
      ["said yes", "Replies labelled Interested."],
      ["replies in all", "Every reply in the list, handled or not."],
    ] }],
    ["h", "Find the replies you want"],
    ["ul", [
      "**Only ones that need a person** is ticked by default. Untick it to see handled replies too.",
      "**Kind of reply** filters by label: Interested, Question, Not now, Wrong person, Needs a look, Not interested, Auto-reply, Unsubscribe or Bounce.",
      "**Channel** filters by Email, LinkedIn, WhatsApp, SMS or Call.",
      "**Assigned to** shows replies for one team member (and unassigned ones), or only Unassigned.",
    ]],
    ["h", "Answer a reply"],
    ["steps", [
      "Read the card. It shows the company, the person, the label, the channel, how long ago it came, the first 400 characters and a **Next step** hint.",
      "Click **Thread** to see every email and message in and out for that lead, oldest first.",
      "Click **Reply** to write an email in the box that opens. It starts with the greeting and quotes what they wrote. When you send it, the reply is marked handled by itself.",
      "If the lead has no email address, the box tells you to call them or answer on their own channel.",
      "If you answered somewhere else, click **Mark handled**. Use **Mark not handled** to undo it.",
    ]],
    ["h", "Other buttons on a card"],
    ["table", { head: ["Button", "What it does"], rows: [
      ["Snooze…", "Pick a time in [[IST]] and press **Snooze**. A task reminds you then. The card shows Snoozed to that time."],
      ["Mark read / Mark unread", "Changes the unread bar. Opening Thread or Reply marks it read for you."],
      ["Assign to…", "Gives the reply to a team member. Their name shows on the card."],
      ["Change label…", "Fix a wrong label. A pencil mark appears next to a label a person corrected."],
    ] }],
    ["tip", "If the portal got a label wrong, correct it. Flows and rules that wait for an Interested reply act on the corrected label."],
    ["h", "Where replies come from"],
    ["p", "Replies appear when they are read from your inbox, picked up by the [[LinkedIn agent]], or pasted on a lead. If the page is empty, check that reply capture is connected on {{menu:Integrations}}. Replied leads stop their sequence by themselves."],
    ["p", "Related: [Outbox & sending](#help/outbox), [Tasks & follow-ups](#help/tasks), [Leads](#help/leads), [Flow builder](#help/flows)."],
  ],
};
