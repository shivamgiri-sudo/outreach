// Flow builder: every step type with its words, icon, outputs, starting settings and one-line summary.
// The server validates the same types (app/flows.py); this file only decides how they look and read.
import { stageLabel } from "../../labels.js";

export const GRID = 20;
export const NODE_W = 220;
export const NODE_H = 68;
export const PORT_GAP = 64;

export const GROUPS = [["trigger", "Triggers"], ["logic", "Logic"], ["action", "Actions"], ["note", "Notes"]];

const ICONS = {
  bolt: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
  userPlus: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M22 11h-6",
  hand: "M18 11V6a2 2 0 0 0-4 0M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8a8 8 0 0 0 16 0v-3a2 2 0 0 0-4 0",
  reply: "M9 17 4 12l5-5M20 18v-2a4 4 0 0 0-4-4H4",
  flag: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7",
  calendar: "M4 5h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM3 10h18M8 3v4M16 3v4",
  rocket: "M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2M12 15l-3-3a22 22 0 0 1 2-4 12.9 12.9 0 0 1 11-6c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2z",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  split: "M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5",
  chat: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  mail: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6",
  whatsapp: "M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z",
  sms: "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM12 18h.01",
  linkedin: "M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-7a6 6 0 0 1 6-6zM2 9h4v12H2zM4 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  phone: "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z",
  tag: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8zM7 7h.01",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  globe: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20",
  stop: "M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9zM9 9h6v6H9z",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 3v4M21 5h-4M5 17v4M7 19H3",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  note: "M5 3h14a2 2 0 0 1 2 2v10l-6 6H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM15 21v-4a2 2 0 0 1 2-2h4",
  plus: "M12 5v14M5 12h14",
  copy: "M9 9h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zM5 15H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1",
  power: "M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10",
};
export const iconPath = (name) => ICONS[name] || ICONS.bolt;

export const REPLY_LABELS = ["interested", "question", "not_now", "wrong_person", "not_interested", "any"];
export const REPLY_OUTPUTS = ["interested", "question", "not_interested", "not_now", "wrong_person", "other", "any", "timeout"];
export const DEFAULT_BRANCHES = ["interested", "question", "not_interested", "timeout"];
export const ACTIVATION_EVENTS = ["demo_opened", "role_drafted", "role_published", "candidate_screened", "interview_booked", "converted"];
export const EMAIL_PLACEHOLDERS = ["greeting", "first_name", "company", "role", "sender", "unsub_link", "demo_link", "offer_line"];
export const TEXT_PLACEHOLDERS = ["greeting", "first_name", "company", "role", "sender", "demo_link"];

export const OUTPUT_LABELS = {
  next: "", yes: "yes", no: "no", a: "A", b: "B", error: "if refused", interested: "interested", question: "question",
  not_interested: "not interested", not_now: "not now", wrong_person: "wrong person", other: "other", any: "any reply", timeout: "no reply",
};
export const BRANCH_HEADINGS = {
  next: "Then", yes: "If yes", no: "If no", a: "Variant A", b: "Variant B", error: "If it is refused",
  interested: "If they reply interested", question: "If they ask a question", not_interested: "If they say no",
  not_now: "If they say not now", wrong_person: "If it is the wrong person", other: "If they reply something else",
  any: "If they reply anything else", timeout: "If there is no reply in time",
};

export const IF_FIELDS = {
  score: ["Score (0–100)", "number"], band: ["Temperature (hot / warm / cold)", "band"], stage: ["Stage", "stage"],
  city: ["City", "text"], country: ["Country", "text"], title: ["Job title", "text"], persona: ["Persona (from title)", "persona"],
  open_roles: ["Open roles", "number"], source: ["Source", "text"], campaign_id: ["Campaign id", "text"], tag: ["Tag", "tags"],
  has_email: ["Has an email", "bool"], has_phone: ["Has a phone number", "bool"], has_consent: ["Has said yes (consent)", "bool"],
  has_meeting: ["Has a meeting", "bool"], has_replied: ["Has replied", "bool"],
};
export const IF_OPS = {
  eq: "is", ne: "is not", gt: "is more than", lt: "is less than", contains: "contains", in: "is one of", exists: "is filled in",
};
export const PERSONAS = ["ceo_founder", "chro", "ta_head", "recruitment_head", "business_head", "general"];
export const opsFor = (kind) => ({
  number: ["gt", "lt", "eq", "ne", "in"], bool: ["eq"], tags: ["contains", "ne", "in", "exists"],
  stage: ["eq", "ne", "in"], band: ["eq", "ne"], persona: ["eq", "ne", "in"],
}[kind] || ["eq", "ne", "contains", "in", "exists"]);

const EMAIL_BODY = "{{greeting}}\n\n\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}";

// type: [group, label, icon, outputs, starting config, help]
export const TYPES = {
  "trigger.manual": ["trigger", "Added by hand", "hand", ["next"], {}, "Start leads from the lead panel (Flows) or pick several in this flow's Enrol box."],
  "trigger.lead_added": ["trigger", "New lead added", "userPlus", ["next"], {}, "Starts for leads added after you activate the flow. Leave filters empty for every new lead."],
  "trigger.reply": ["trigger", "They reply", "reply", ["next"], { label: "interested" }, "Starts when a reply is read and acted on (email, WhatsApp, SMS, LinkedIn)."],
  "trigger.stage_changed": ["trigger", "Stage changes", "flag", ["next"], { stage: "interested" }, "Starts when someone moves the lead to this stage."],
  "trigger.meeting_booked": ["trigger", "Meeting booked", "calendar", ["next"], {}, "Starts when a meeting is booked in the lead panel or through Cal.com / Calendly."],
  "trigger.activation_event": ["trigger", "Product activity", "rocket", ["next"], { event: "role_published" }, "Starts when BookMyInterview reports this activity for the lead."],
  "trigger.no_reply_days": ["trigger", "No reply for some days", "clock", ["next"], { days: 14 }, "Starts for leads still in the cold stages who got an email and haven't replied for this many days."],
  if: ["logic", "If / else", "branch", ["yes", "no"], { field: "score", op: "gt", value: 60 }, "Checks the lead and goes down the yes or no path."],
  split: ["logic", "A/B split", "split", ["a", "b"], { percent_a: 50 }, "Sends a share of leads down path A, the rest down B. A lead always gets the same variant."],
  wait: ["logic", "Wait", "clock", ["next"], { days: 2, hours: 0, business_hours: true }, "Pauses the lead here. 'Until business hours' moves the end to Mon–Fri 9:00–18:00 IST."],
  wait_for_reply: ["logic", "Wait for a reply", "chat", null, { days: 3, branches: DEFAULT_BRANCHES }, "Waits up to N days. A reply goes down its path (or 'any reply'); no reply goes down 'no reply'."],
  email: ["action", "Send email", "mail", ["next", "error"], { subject: "", body: EMAIL_BODY }, "Goes through the email sequence engine: practice mode, daily cap, warm-up, one email per company a day, Mon–Fri 9–18 IST."],
  whatsapp: ["action", "Send WhatsApp", "whatsapp", ["next", "error"], { template: "first_after_yes" }, "Only with recorded consent. Approved templates only. Practice until WhatsApp is connected and switched on."],
  sms: ["action", "Send SMS", "sms", ["next", "error"], { text: "Hi {{first_name}}, {{sender}} from BookMyInterview. Reply STOP to opt out." }, "Only with recorded consent. Must match your DLT template. Practice until SMS is connected and switched on."],
  linkedin_task: ["action", "LinkedIn task", "linkedin", ["next", "error"], { mode: "task", message: "Hi {{first_name}}, came across your profile and would be glad to connect." }, "A person sends LinkedIn messages by hand; the flow only makes the task."],
  call_task: ["action", "Call task", "phone", ["next"], {}, "A task for the caller, due at the next call slot (same day before 18:30 IST, else next weekday 11:00)."],
  meeting_link: ["action", "Meeting link", "calendar", ["next", "error"], {}, "With a booking-link calendar (Cal.com, Calendly, any link) it prepares the link and asks a person to send it; otherwise a 'book a meeting' task."],
  set_stage: ["action", "Set stage", "flag", ["next", "error"], { stage: "not_now" }, "Moves the lead only if the stage rules allow it. Closed leads stay closed."],
  add_tag: ["action", "Add tag", "tag", ["next"], { tag: "" }, "Adds a tag you can filter on later."],
  notify: ["action", "Notify a person", "bell", ["next"], { message: "" }, "An in-app notification (and an email when reminder emails are on)."],
  webhook: ["action", "Webhook", "globe", ["next", "error"], { url: "", include_contact: false }, "POSTs the lead's basic details as JSON to a public https address."],
  stop: ["action", "Stop", "stop", [], {}, "Ends the flow for this lead."],
  "trigger.schedule": ["trigger", "On a schedule", "clock", ["next"], { when: "weekdays", hour: 10, limit: 30 },
    "Runs by itself at the hour you pick (India time) and starts matching leads that are not in the flow yet, up to a limit."],
  ai: ["action", "Ask Claude", "sparkles", ["next", "error"], { mode: "draft", prompt: "", save_as: "answer", options: [] },
    "Claude writes a draft, picks one of your options, or pulls out a detail. The answer is saved under a name you can use as {{name}} in later steps."],
  set_vars: ["action", "Set fields", "sliders", ["next"], { values: [{ name: "pitch", value: "" }] },
    "Make named fields from the lead's details, for example pitch = Hiring for {{role}} at {{company}}. Use them as {{pitch}} in later steps."],
  switch: ["logic", "Switch", "split", null, { field: "band", cases: ["hot", "warm"] },
    "Looks at one value and sends the lead down the matching path (up to 4), or 'other' when nothing matches."],
  http: ["action", "HTTP request", "globe", ["next", "error"], { method: "GET", url: "", body: "", save_as: "" },
    "Calls a public https address and can save the answer for later steps. Private and internal addresses are always refused."],
  note: ["note", "Sticky note", "note", [], { text: "" }, "A comment on the canvas for you and your team. It never runs."],
};

export const groupOf = (type) => TYPES[type]?.[0] || "action";
export const labelOf = (type) => TYPES[type]?.[1] || type;
export const iconOf = (type) => TYPES[type]?.[2] || "bolt";
export const helpOf = (type) => TYPES[type]?.[5] || "";
export const isTrigger = (type) => String(type).startsWith("trigger.");
export const titleOf = (node) => (node.config?.title || "").trim() || labelOf(node.type);
export const startingConfig = (type) => JSON.parse(JSON.stringify(TYPES[type]?.[4] || {}));

export function outputsOf(node, edges = []) {
  const fixed = TYPES[node.type]?.[3];
  if (node.type === "switch") {
    const cases = (node.config?.cases || []).filter((v) => String(v).trim() !== "").slice(0, 4);
    const used = edges.filter((e) => e.from === node.id).map((e) => e.label).filter((l) => /^c[1-4]$/.test(l));
    const count = Math.max(cases.length, ...used.map((l) => Number(l[1])), 1);
    return [...Array.from({ length: count }, (_, i) => `c${i + 1}`), "other"];
  }
  if (fixed) return fixed;
  const chosen = node.config?.branches?.length ? node.config.branches : DEFAULT_BRANCHES;
  const used = edges.filter((e) => e.from === node.id).map((e) => e.label);
  return REPLY_OUTPUTS.filter((o) => chosen.includes(o) || used.includes(o));
}

// The words shown on a path leaving a step. A Switch shows the value each path matches.
export function outputLabel(node, label) {
  if (node?.type === "switch") {
    if (label === "other") return "other";
    const value = (node.config?.cases || [])[Number(label.slice(1)) - 1];
    return value ? String(value) : label;
  }
  return OUTPUT_LABELS[label] ?? label;
}

const host = (url) => { try { return new URL(url).host; } catch { return url || "no address yet"; } };

export function summaryOf(node, caller = "Priya") {
  const c = node.config || {};
  switch (node.type) {
    case "trigger.manual": return "Start from the lead panel or in bulk";
    case "trigger.lead_added": return ["source", "city", "country", "tag", "campaign_id"].filter((k) => c[k]).map((k) => `${k}: ${c[k]}`).join(", ") || "Any new lead";
    case "trigger.reply": return `Reply: ${OUTPUT_LABELS[c.label] || c.label || "pick one"}`;
    case "trigger.stage_changed": return `Stage becomes ${stageLabel(c.stage || "")}`;
    case "trigger.meeting_booked": return "A meeting is booked";
    case "trigger.activation_event": return `In the product: ${(c.event || "").replace(/_/g, " ")}`;
    case "trigger.no_reply_days": return `No reply ${c.days || "?"} days after an email`;
    case "if": return `${IF_FIELDS[c.field]?.[0] || "?"} ${IF_OPS[c.op] || "?"}${c.op === "exists" ? "" : ` ${c.field?.startsWith("has_") ? (String(c.value) === "true" ? "yes" : "no") : c.value ?? ""}`}`;
    case "split": return `A ${c.percent_a ?? 50}% · B ${100 - (Number(c.percent_a) || 50)}%`;
    case "wait": return `${c.days || 0} day(s) ${c.hours || 0} h${c.business_hours ? " · then business hours" : ""}`;
    case "wait_for_reply": return `Up to ${c.days || "?"} day(s)`;
    case "email": return c.subject || "No subject yet";
    case "whatsapp": return `Template: ${c.template || "?"}`;
    case "sms": return c.text || "No text yet";
    case "linkedin_task": return c.mode === "start_sequence" ? "Start the LinkedIn sequence" : "One task with your message";
    case "call_task": return `For ${c.owner || caller}`;
    case "meeting_link": return `Booking link or task for ${c.owner || caller}`;
    case "set_stage": return `→ ${stageLabel(c.stage || "")}`;
    case "add_tag": return c.tag ? `#${c.tag}` : "No tag yet";
    case "notify": return `To ${c.to || caller}: ${c.message || "…"}`;
    case "webhook": return `POST to ${host(c.url)}`;
    case "trigger.schedule": return `${{ daily: "Every day", weekdays: "Every weekday", weekly: "Every week" }[c.when] || "Pick when"} at ${String(c.hour ?? 10).padStart(2, "0")}:00 IST`;
    case "ai": return `${{ draft: "Writes a draft", classify: "Picks: " + (c.options || []).join(", "), extract: "Pulls out a detail" }[c.mode] || "Pick what Claude does"} → {{${c.save_as || "?"}}}`;
    case "set_vars": return (c.values || []).map((v) => v.name).filter(Boolean).join(", ") || "No fields yet";
    case "switch": return `On ${c.field?.startsWith("var:") ? c.field.slice(4) : IF_FIELDS[c.field]?.[0] || "?"}`;
    case "http": return `${c.method || "GET"} ${host(c.url)}`;
    case "note": return c.text || "Empty note";
    case "stop": return "End here";
    default: return "";
  }
}
