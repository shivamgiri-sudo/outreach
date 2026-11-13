// Has anyone tried this lead yet? Same rule as app/contact_status.py.
const TRY_KINDS = ["email", "linkedin", "whatsapp", "call"];
const REACHED = ["connected", "interested", "meeting booked", "meeting_booked", "call back", "call_back"];
const isReply = (e) => e.kind === "reply" && !String(e.text || "").startsWith("[auto_reply]") && !String(e.text || "").startsWith("[bounce]");

export const CONTACT_STATUSES = [["never", "Never contacted"], ["tried", "Tried, no answer"], ["connected", "Connected"], ["replied", "Replied"]];

export function contactOf(lead) {
  if (lead.contact_status) return { status: lead.contact_status, attempts: lead.contact_attempts || 0 };   // the server read every outbox and log
  const events = lead.timeline || [];
  const tries = events.filter((e) => TRY_KINDS.includes(e.kind));
  const replied = events.some(isReply);
  const reached = tries.some((e) => e.kind === "call" && REACHED.some((w) => String(e.text || "").toLowerCase().includes(w)));
  const status = replied ? "replied" : reached ? "connected" : tries.length ? "tried" : "never";
  return { status, attempts: tries.length };
}

const CLOSED = ["unsubscribed", "disqualified", "not_now", "customer", "advocate"];   // same as the call queue
// Fresh to attempt: never contacted, still open, not on the do-not-contact list, and something to reach them on.
export const isFresh = (l) => contactOf(l).status === "never" && !l.do_not_contact && !CLOSED.includes(l.stage) && Boolean(l.phone || l.email);
