// Recipe: start LinkedIn outreach safely and understand why it stops.
export default {
  id: "recipe-linkedin-safely", title: "Start LinkedIn outreach safely", group: "How-to recipes", order: 40, kind: "recipe",
  summary: "Set low daily limits, try the agent without clicking anything, and know why it stops and what to do.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "LinkedIn does not allow automation and can restrict accounts that use it, even at low volume. Use a LinkedIn account you can live without.",
      "Owners and workspace admins change limits and switch the agent on. Callers and viewers see the status only.",
      "Leads need a LinkedIn link to be started. You can work by hand without any agent: {{menu:LinkedIn outreach}} lists today's messages to copy and send yourself.",
      "The notes and messages come from the LinkedIn sequence. Only owners and admins edit them.",
    ]],
    ["p", "The [[LinkedIn agent]] is a program that sends the connection requests and follow-up messages for you, one action at a time, either from the cloud or from an [[Office computer]]. You log in to LinkedIn yourself in the Chrome window it opens. This portal never receives your password."],
    ["h", "Steps"],
    ["steps", [
      "Open {{menu:LinkedIn outreach}}. Open **LinkedIn settings**. An owner sets **New connection requests per day** (the page suggests 10 to 15; the default is 12; the highest allowed is 40) and **Who sends from the LinkedIn account**. Click **Save**.",
      "In the **LinkedIn agent** box, open **Limits**. Keep these low: **Connection requests per day**, **Per week**, **Messages per day** and **Minutes between two actions** (3 to 60; the default gap is 6). Check **Start hour** and **End hour** (India time) and keep **Monday to Friday only** ticked. Click **Save limits**.",
      "Leave **When LinkedIn will not allow a note ... send the connection request without one** unticked unless you accept requests without a note.",
      "To run from your own computer, open **Advanced: run on your own computer**. Create a pairing token in the **Chrome assistant** box, then follow the numbered steps there. Add `--dry` to the command first. In dry mode it says what it would do and clicks nothing.",
      "A Chrome window opens. Log in to LinkedIn in that window yourself, including any code LinkedIn sends.",
      "When the dry run looks right, stop it and start the agent again without `--dry`. Then click **Switch on** in the **LinkedIn agent** box.",
      "Pick a few people. Under **Suggested people to connect with today**, click **Start LinkedIn sequence** for each. Start with a handful.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The state label beside **LinkedIn agent** shows **Running**. If it says **On, but the agent program is not running**, the program on your computer is off.",
      "The line starting **Today:** shows requests and messages sent and how many are left today.",
      "**What it did lately** lists each action, such as **Connection request sent** or **Accepted**.",
    ]],
    ["h", "Why it stops, and what the guard does"],
    ["table", { head: ["What you see", "Why and what to do"], rows: [
      ["Switched off, or outside working hours", "The switch is off, or it is outside your start and end hours. Nothing to fix; it carries on in the next window."],
      ["Nothing is sent and \"left today\" shows 0", "The **Today:** line shows how many requests and messages are left. At 0 the daily or weekly limit is used. It carries on the next day or week."],
      ["Paused: (a note) beside a LinkedIn account", "The agent saw a login wall, a security check, a CAPTCHA, the weekly invitation limit or an \"account restricted\" message. It stops itself, switches off and tells the owner. Sort it out on LinkedIn first. Then an owner or admin clicks **Resume** in the accounts table and, if needed, **Switch on**."],
      ["Stopped by the platform admin / Every LinkedIn agent is stopped", "Mas Callnet used the stop-all switch. Wait for them to click **Let agents run again**."],
      ["tried 2 times in the last 24 hours without success; held until (a time) IST so the agent does not loop on this person", "A safety guard. If the agent opens one person twice in 24 hours and nothing gets sent or accepted, that person is held for 24 hours from the first try. A sent step, an acceptance or a reply clears the count. Nothing to fix; check the profile by hand if it keeps happening."],
    ] }],
    ["warn", "If the agent says it hit a security check or a restricted account, do not switch it straight back on. Log in to LinkedIn by hand, follow LinkedIn's steps, and lower the limits before you resume."],
    ["tip", "A tool such as Waalaxy or PhantomBuster is your own choice and risk. The **Use a LinkedIn tool** card exports a list (owners) and imports its results file to mark who accepted or replied."],
  ],
};
