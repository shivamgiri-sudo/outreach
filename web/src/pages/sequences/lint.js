// Checks shown while a sequence step is edited. "error" blocks saving (the server refuses it too); "warn" is advice.
// Brand placeholders come from Setup → Brand & offer and are filled the same way in every channel.
export const BRAND_PLACEHOLDERS = ["my_company", "my_product", "my_tagline", "my_value_1", "my_value_2", "my_value_3", "my_cta", "my_website", "sign_off"];
export const PLACEHOLDERS = ["greeting", "first_name", "company", "role", "sender", "unsub_link", "offer_line", "demo_link", ...BRAND_PLACEHOLDERS];
const known = (p) => PLACEHOLDERS.includes(p) || /^custom\.[a-z][a-z0-9_]{0,39}$/.test(p);   // plus the owner's custom lead fields
export const SPAM_WORDS = ["free", "guarantee", "guaranteed", "act now", "urgent", "limited time", "risk-free", "risk free", "100%",
  "winner", "cash", "click here", "buy now", "no obligation", "order now", "special promotion", "earn money", "lowest price",
  "congratulations", "exclusive deal", "dear friend", "once in a lifetime", "!!!", "$$$"];
const MAX_SUBJECT = 200;
const LONG_SUBJECT = 60;
const LONG_BODY_WORDS = 180;

// One human day for a step: day 0 (the first email) is "Day 1".
export const humanDay = (day) => Number(day) + 1;
// The default step names end in "(Day N)"; the day is shown once, from the number.
export const stepTitle = (name) => String(name || "").replace(/\s*\(Day \d+\)\s*$/, "");

const placeholdersIn = (text) => [...String(text || "").matchAll(/\{\{(\w+(?:\.\w+)?)\}\}/g)].map((m) => m[1]);

export function lintText(subject, body, { needsUnsub = true } = {}) {
  const out = [];
  const subj = String(subject || "");
  const text = String(body || "");
  if (subj.length > MAX_SUBJECT) out.push({ level: "error", text: `Subject is ${subj.length} characters; the limit is ${MAX_SUBJECT}.` });
  else if (subj.length > LONG_SUBJECT) out.push({ level: "warn", text: `Subject is ${subj.length} characters; phones cut it off after about ${LONG_SUBJECT}.` });
  if (subj && subj === subj.toUpperCase() && /[A-Z]{4,}/.test(subj)) out.push({ level: "warn", text: "Subject in capitals reads as shouting and trips spam filters." });
  if (needsUnsub && text && !text.includes("{{unsub_link}}")) out.push({ level: "error", text: "The body must contain {{unsub_link}} (every cold email carries the unsubscribe link)." });
  const unknown = [...new Set([...placeholdersIn(subj), ...placeholdersIn(text)].filter((p) => !known(p)))];
  if (unknown.length) out.push({ level: "error", text: `Unknown placeholder(s): ${unknown.map((p) => `{{${p}}}`).join(", ")}.` });
  const stripped = (subj + text).replace(/\{\{\w+(?:\.\w+)?\}\}/g, "");
  if (stripped.includes("{{") || stripped.includes("}}")) out.push({ level: "error", text: "Write placeholders exactly like {{company}} (no spaces, both braces)." });
  const lower = `${subj} ${text}`.toLowerCase();
  const spam = SPAM_WORDS.filter((w) => (/^[a-z -]+$/.test(w) ? new RegExp(`\\b${w}\\b`).test(lower) : lower.includes(w)));
  if (spam.length) out.push({ level: "warn", text: `Spam-filter words: ${spam.join(", ")}.` });
  const words = text.split(/\s+/).filter(Boolean).length;
  if (words > LONG_BODY_WORDS) out.push({ level: "warn", text: `${words} words; cold emails under ${LONG_BODY_WORDS} words get more replies.` });
  const links = (text.match(/https?:\/\//g) || []).length + placeholdersIn(text).filter((p) => p === "demo_link").length;
  if (links > 1) out.push({ level: "warn", text: "More than one link: cold emails with links land in spam more often." });
  return out;
}

export function lintStep(step, prev) {
  const out = lintText(step.subject, step.body);
  if (!String(step.subject || "").trim()) out.unshift({ level: "error", text: "Write a subject." });
  if (!String(step.body || "").trim()) out.unshift({ level: "error", text: "Write the email." });
  if (prev && Number(step.day) <= Number(prev.day)) out.unshift({ level: "error", text: "This step must come on a later day than the one before." });
  else if (prev && Number(step.day) - Number(prev.day) < 2) out.push({ level: "warn", text: "Less than 2 days after the previous email: the engine waits 2 days anyway." });
  (step.variants || []).forEach((v) => {
    if (!String(v.name || "").trim()) out.push({ level: "error", text: "Every variant needs a name." });
    if (!String(v.subject || "").trim() && !String(v.body || "").trim()) out.push({ level: "error", text: `Variant ${v.name || "?"} changes nothing.` });
    lintText(v.subject, v.body, { needsUnsub: Boolean(String(v.body || "").trim()) })
      .forEach((x) => out.push({ ...x, text: `Variant ${v.name || "?"}: ${x.text}` }));
  });
  return out;
}
