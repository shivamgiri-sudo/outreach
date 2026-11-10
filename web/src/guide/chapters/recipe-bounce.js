// Recipe: handle a bounce.
export default {
  id: "recipe-bounce", title: "Handle a bounce", group: "How-to recipes", order: 100, kind: "recipe",
  summary: "What a bounced email means, where you see it, what the app does by itself, and what you should do.",
  blocks: [
    ["h", "Before you start"],
    ["p", "A [[Bounce|bounce]] means the email could not be delivered. A [[Hard bounce|hard bounce]] is permanent, for example the mailbox does not exist. A [[Soft bounce|soft bounce]] is temporary, for example a full mailbox. Anyone can read the places below. Only owners resume sending."],
    ["h", "What the app does by itself"],
    ["ul", [
      "It adds the address to the do-not-contact list, so it is never emailed again.",
      "It stops that lead's email sequence. The lead's sequence line reads \"Stopped (bounced) — the address bounced\".",
      "It moves the lead to **Disqualified** (unless the lead is already unsubscribed, a customer or an advocate).",
      "It adds a line to the lead's timeline: \"Email provider reported a bounce for (address)\".",
      "It marks the address as invalid in the prospect database, so it is not offered again.",
      "It counts the bounce toward the sending health check. If more than 2% of emails bounce, or more than 0.3% become spam complaints, sending pauses by itself once at least 50 emails were sent in 7 days. The same applies if a bounce notice reaches the replies inbox.",
    ]],
    ["h", "Where you see bounces"],
    ["table", { head: ["Place", "What you see"], rows: [
      ["{{menu:Outbox & sending}}", "The top card shows the last 7 days: how many sent, **bounced** with a percentage, and spam complaints. A pause shows as **Sending is paused.** with the reason."],
      ["{{menu:Replies}}", "A bounce notice that arrives as a reply is labelled **Bounce**."],
      ["The lead ({{menu:Leads}})", "The email sequence line says **Stopped (bounced)**, the stage is **Disqualified**, and the timeline has the provider line."],
      ["{{menu:Data hygiene}}", "The count **on do-not-contact list** and the count of **invalid emails** in the report. Use it to find and clean the same kind of address before you send."],
    ] }],
    ["h", "What you should do"],
    ["steps", [
      "Open the lead and read the address. Look for a typo such as .con or gmial.",
      "If it was a typo, find the right address and bring it in as a new lead (see [import a sheet or Excel file](#help/recipe-import-sheet)). The old address stays blocked, which is correct.",
      "If many bounced at once, stop and look at the list they came from. Run {{menu:Data hygiene}} on the list before sending more.",
      "If sending is paused and shows a restart time, it restarts by itself. If it has no restart time, an owner clicks **Resume sending now** on {{menu:Outbox & sending}} after the list is cleaned.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The bounced lead no longer appears in today's sends.",
      "The 7-day bounce percentage on {{menu:Outbox & sending}} falls as new emails go to good addresses.",
    ]],
    ["warn", "Do not resume sending on a list that is still full of bad addresses. A high bounce rate harms your sending name and can get mail from your domain blocked. For a client account, a high bounce or complaint rate puts the whole account's sending on hold until a super admin releases it."],
    ["tip", "Import with a clean list. The import step already leaves out addresses that are not valid and lists emails it fixed. See [import a sheet or Excel file](#help/recipe-import-sheet)."],
  ],
};
