// Page chapter: Prompt generator.
export default {
  id: "prompts", title: "Prompt generator", group: "Menu pages", order: 220, kind: "page", masOnly: true, roles: ["owner", "caller", "viewer"], menu: "Prompt generator",
  summary: "Builds a ready-to-use instruction (a prompt) for an AI writer, for any industry, role and channel, with the honesty rules always added.",
  blocks: [
    ["p", "{{menu:Prompt generator}} writes a system prompt: a set of instructions you can give to an [[AI]] tool so it writes messages in your style. You choose the channel, language, tone and goal. The honesty rules are always added last and override everything above them, including your Do and Don't lines."],
    ["h", "Who can use it"],
    ["ul", [
      "Only Mas Callnet staff logins see this page. Client company logins do not.",
      "Owners and callers can click **Generate prompt**. Viewers cannot.",
    ]],
    ["h", "Make a prompt"],
    ["steps", [
      "Pick the **Channel** (email, whatsapp or voice), **Language** (English, Hindi or Hinglish) and **Tone** (warm, friendly, formal or playful).",
      "Type the **Industry**, the **Role being hired** and the **Goal**.",
      "Optionally add facts or a customer story you may use (only with permission), and your **Do** and **Don't** lines.",
      "Tick or untick **Write it with Claude**. Ticked gives richer wording. Unticked uses a built-in template.",
      "Click **Generate prompt**. The result appears under **Your prompt**. Click **Copy**.",
    ]],
    ["h", "The honesty rules"],
    ["p", "The app adds these itself, last, so nobody can switch them off: no invented numbers, customers or stories; WhatsApp only after a yes; calls through PD only before 18:55; and an opt-out in every message."],
    ["tip", "A note under the heading tells you whether Claude wrote the prompt or the built-in template was used (for example when Claude is not connected)."],
    ["p", "Related: [Success stories](#help/stories), [Sequences & templates](#help/templates)."],
  ],
};
