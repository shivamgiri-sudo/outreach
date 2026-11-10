// Recipe: reset someone's two-step login.
export default {
  id: "recipe-reset-two-step", title: "Reset someone's two-step login", group: "How-to recipes", order: 140, kind: "recipe",
  roles: ["owner"],
  summary: "Help a person who lost their phone or recovery codes by switching their two-step login off so they can set it up again.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only owners can do this. Admins and callers cannot; they should ask an owner.",
      "Only a super admin can reset another owner's two-step login. Nobody can reset a super admin's own two-step login here: you cannot reset your own, and only another super admin can reset a super admin's. A super admin who is locked out uses their recovery codes with a password reset link, or asks another super admin.",
      "Confirm who is asking. Phone them or ask in person. Resetting removes a layer of protection for that login.",
    ]],
    ["p", "[[Two-step login]] asks for a six-digit code from the person's phone after the password. If they lost the phone and their [[Recovery code|recovery codes]], an owner resets it."],
    ["h", "Steps"],
    ["steps", [
      "Open {{menu:Security}} in Setup.",
      "In the **Two-step login** card, find the person in the list. Their label reads **Two-step on**.",
      "Click **Reset two-step** next to their name.",
      "A box asks: Reset two-step login for (their email)? They sign in with the password only. If you required two-step for everyone it adds: and set it up again. Click **Reset**.",
      "Tell the person to sign in with their password. If two-step is required for everyone, they are asked to set it up again at once. Otherwise they can turn it on again under {{menu:My password & two-step}}.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "A message says **Two-step login reset for (email).**",
      "The person's label changes to **Password only**.",
      "Under {{menu:Team members}} the **Two-step** column for that person reads **Off**.",
      "The reset is recorded in the change log as a two-step reset by an owner.",
    ]],
    ["warn", "If they forgot their password too, reset the password separately: {{menu:Team members}}, **Manage**, **Reset password…**. Give them the new password in person or by phone, never in a group chat."],
    ["tip", "The switch **Require two-step login for everyone** is on the same card. Switch it on once everyone has set up their phone."],
  ],
};
