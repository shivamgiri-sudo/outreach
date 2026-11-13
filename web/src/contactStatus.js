// Has anyone tried this lead yet? Same rule as app/contact_status.py.
const TRY_KINDS = ["email", "linkedin", "whatsapp", "call"];
const REACHED = /\b(connected|interested|meeting[ _]booked|call[ _]back)\b/i;   // word boundaries: "call disconnected" is not "connected"
const isReply = (e) => e.kind === "reply" && !String(e.text || "").startsWith("[auto_reply]") && !String(e.text || "").startsWith("[bounce]");

export const CONTACT_STATUSES = [["never", "Never contacted"], ["tried", "Tried, no answer"], ["connected", "Connected"], ["replied", "Replied"], ["invalid", "Invalid lead"], ["consultant", "Consultant lead"]];

export function contactOf(lead) {
  if (lead.lead_quality?.kind) return { status: lead.lead_quality.kind, attempts: lead.contact_attempts || 0 };   // a caller's verdict, not an attempt
  if (lead.contact_status) return { status: lead.contact_status, attempts: lead.contact_attempts || 0 };   // the server read every outbox and log
  const events = lead.timeline || [];
  const tries = events.filter((e) => TRY_KINDS.includes(e.kind));
  const replied = events.some(isReply);
  const reached = tries.some((e) => e.kind === "call" && REACHED.test(String(e.text || "")));
  const status = replied ? "replied" : reached ? "connected" : tries.length ? "tried" : "never";
  return { status, attempts: tries.length };
}

const CLOSED = ["unsubscribed", "disqualified", "not_now", "customer", "advocate"];   // same as the call queue
// Fresh to attempt: never contacted, still open, not on the do-not-contact list, and something to reach them on.
export const isFresh = (l) => contactOf(l).status === "never" && !l.do_not_contact && !CLOSED.includes(l.stage) && Boolean(l.phone || l.email);
