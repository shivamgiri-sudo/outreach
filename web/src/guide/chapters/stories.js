// Page chapter: Success stories.
export default {
  id: "stories", title: "Success stories", group: "Menu pages", order: 210, kind: "page", masOnly: true, roles: ["owner", "caller", "viewer"], menu: "Success stories",
  summary: "A library of real customer results you may share, with a permission tick so nothing is used without consent.",
  blocks: [
    ["p", "{{menu:Success stories}} is a small library of customer results. Add only real results with the customer's permission. Stories without permission are never sent."],
    ["h", "Who can use it"],
    ["ul", [
      "Only Mas Callnet staff logins see this page. Client company logins do not.",
      "Owners and callers can add stories. Viewers see the library only.",
    ]],
    ["h", "Add a story"],
    ["steps", [
      "In **Add a story**, type a **Title**, for example \"Retail chain filled 12 roles\".",
      "Fill **Industry** and **Result (real numbers only)**. **Quote (optional)** adds the customer's words.",
      "Tick **The customer allowed us to share this** only if you have their permission.",
      "Click **Save story**. The button works once there is a title.",
    ]],
    ["h", "The library"],
    ["p", "Each story shows its title, industry and a tag: **OK to share** (green) or **No permission yet** (orange), then the result and quote."],
    ["warn", "Never invent numbers or customers. A story without the permission tick must not be used in any message."],
    ["p", "Related: [Prompt generator](#help/prompts), [Sequences & templates](#help/templates)."],
  ],
};
