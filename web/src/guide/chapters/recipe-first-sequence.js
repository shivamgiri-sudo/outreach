// Recipe: run a first email sequence safely.
export default {
  id: "recipe-first-sequence", title: "Run a first email sequence safely", group: "How-to recipes", order: 30, kind: "recipe",
  roles: ["owner", "admin"],
  summary: "Practise first with nothing leaving the app, then switch on real sending with the right limits.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "An owner can switch real sending on and off and change the daily limit. An admin can create sequences and start leads on them. A caller can start a lead on a sequence.",
      "You need a few leads that have an email address ({{menu:Leads}}).",
      "Real sending also needs: a public https web address (Setup, {{menu:Connections}}, **The basics**), the cold-email sending domain connected under {{menu:Integrations}}, and your company name plus a postal address (or website and contact email) for the email footer. The footer is set on {{menu:Outbox & sending}}.",
    ]],
    ["p", "Until real sending is on, the app is in [[Practice mode]]. Emails are written and recorded as **practice** but never leave the app."],
    ["h", "Part 1: practise"],
    ["steps", [
      "Open {{menu:Sequences & templates}}. On the **Email sequences** tab, look at the built-in sequence. Click **Steps**, then **Preview designed email** to see an email as the lead will see it.",
      "To write your own, an owner or admin clicks **New sequence**, or **Duplicate** on an existing one, and edits the subject and body. Keep the unsubscribe line in every email.",
      "Open one lead. Pick your sequence in the **Email sequence to start** drop-down and click the button beside it (it reads, for example, **Start 6-step email sequence**). Or tick several leads on {{menu:Leads}} and use **Start email sequence** in the bar that appears.",
      "Open {{menu:Outbox & sending}}. An owner clicks **Run sequences now**. The message says, for example, \"1 email(s) practised (not sent)\".",
      "Check the table. Rows show the status **practice**. Open the lead to read what would have been sent. Fix anything wrong in the sequence.",
      "When you are happy, an owner clicks **Clear practice runs**. This removes the practice rows so real sending starts every lead from Email 1.",
    ]],
    ["h", "Part 2: send for real"],
    ["steps", [
      "On {{menu:Outbox & sending}}, find the **Send for real** card. If it lists things to finish (\"Finish these first: ...\"), do those first. **Switch on…** is greyed out until they are done.",
      "Check **Max emails per day**. Start small. The default is 20. Change it and click **Save settings**.",
      "Click **Switch on…**. Type **SEND FOR REAL** in the box and click **Switch on real sending**.",
      "The top card now says **Real sending is ON**. Emails go out only on weekdays, 9am to 6pm India time ([[IST]]), a few per run, one email per company per day, with at least two days between a lead's emails.",
      "A new sending address is eased in ([[Warm-up]]): at most 10 emails a day in the first week, 20 in the second week, 35 in the third, then your **Max emails per day**.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The **Send for real** card shows **On: emails go to real people** and who switched it on and when.",
      "Rows in the table change from **practice** to **sent**.",
      "The line under the top card shows the last 7 days: sent, bounced and spam complaints.",
    ]],
    ["warn", "Sending pauses by itself when more than 2% of emails bounce or more than 0.3% become spam complaints. That protects your sending name. Do not resume until you have cleaned the list. A reply or an unsubscribe stops that lead's sequence at once."],
    ["tip", "If an email does not go, read [what to do when sending is held](#help/recipe-sending-held). **Switch off** on the same card stops real sending in one click."],
  ],
};
