// Page chapter: Tasks & follow-ups.
export default {
  id: "tasks", title: "Tasks & follow-ups", group: "Menu pages", order: 150, kind: "page", menu: "Tasks & follow-ups",
  summary: "Everything that needs doing, sorted by due time, with one-click done, snooze and reassign.",
  blocks: [
    ["p", "{{menu:Tasks & follow-ups}} is your to-do list. Tasks come from you and from the portal. For example, when a lead says yes, a call task for the caller is added automatically. Due times are in [[IST]]. Reminders arrive 15 minutes before a task is due, and again if it is an hour overdue."],
    ["h", "Who can use it"],
    ["ul", [
      "Everyone can read tasks and download them.",
      "Owners, admins and callers can add, finish, snooze, reprioritise and reassign tasks.",
      "Viewers see the lists only.",
      "Only an owner sees the **Team & reminders** box.",
    ]],
    ["h", "Add a task"],
    ["steps", [
      "Type the task in the top box, for example \"Send case study to Acme\".",
      "Optionally set a due date and time (IST), an owner and a priority (High, Normal or Low).",
      "Click **Add**.",
    ]],
    ["h", "The five lists"],
    ["table", { head: ["List", "What is in it"], rows: [
      ["Overdue", "Past their due time. The number turns red when there are any."],
      ["Today", "Due today."],
      ["Upcoming", "Due later."],
      ["No due time", "Tasks without a time."],
      ["Done recently", "Finished tasks, with the time they were done."],
    ] }],
    ["h", "Work through a task"],
    ["ul", [
      "Click the company name to open the lead.",
      "**Done** marks it finished.",
      "**+1 hour** pushes it back an hour. **Tomorrow** moves it to the next day.",
      "The priority menu changes High, Normal or Low. **Reassign** moves it to another team member.",
    ]],
    ["h", "Filter, sort and download"],
    ["ul", [
      "**Show tasks for** picks one person or Everyone.",
      "**Order by** switches between Due time and Priority.",
      "Choose which tasks to download, then click **Download CSV**. The file has due times in IST.",
    ]],
    ["h", "Team & reminders (owners)"],
    ["p", "Click **Team & reminders** to set the **Caller's name** (who gets the \"call them\" tasks) and the team list with name, email and role. Tick the box to also email each person their reminders. This needs an email provider connected on {{menu:Integrations}}. Click **Save team**."],
    ["tip", "If a lead unsubscribed, its tasks show a \"lead unsubscribed\" tag. Do not email them."],
    ["p", "Related: [My day](#help/myday), [Call desk](#help/calls), [Replies](#help/replies)."],
  ],
};
