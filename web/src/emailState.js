// How sure are we that a lead's email reaches a real mailbox? The same rules as app/email_state.py (the server puts
// `email_state` on every lead it sends; computeEmailState is the fallback and is checked against the Python helper in
// tests/test_email_state.py). No imports, so plain node can run it.
const GUESS_SOURCES = ["guess:", "pattern:"];
const PROBE_CONFIRMED = "the mail server confirmed this mailbox";
const DOMAIN_ONLY_PROVIDERS = ["builtin"];
const addr = (v) => String(v || "").trim().toLowerCase();
const has = (list, value) => (list instanceof Set ? list.has(value) : (list || []).includes(value));

export const EMAIL_STATES = {
  confirmed: { label: "Mailbox confirmed", tone: "success", tip: "A mailbox check said this mailbox exists, or an email to it was delivered before." },
  domain_ready: { label: "Domain ready", tone: "warning", tip: "The domain takes mail, but the mailbox itself is not confirmed. Emails to it go out at a lower daily volume." },
  candidate: { label: "Guess only", tone: "neutral", tip: "Only a guess from the company's email format. It is never emailed until someone confirms it." },
  bad: { label: "Bad address", tone: "danger", tip: "A check said this address does not work, or an email to it bounced. It is not emailed." },
};
// The Leads filter "Email": [value, label].
export const EMAIL_STATE_FILTERS = [["confirmed", "Confirmed"], ["domain_ready", "Domain ready"], ["candidate", "Guess only"], ["bad", "Bad"]];

export function mailboxConfirmed(check = {}) {
  if (check.status !== "valid") return false;
  if (!DOMAIN_ONLY_PROVIDERS.includes(String(check.provider || ""))) return true;
  return (check.reasons || []).some((r) => String(r).includes(PROBE_CONFIRMED));
}

// history: { bounced: [addresses] | Set, delivered: [addresses] | Set } (lower-case).
export function computeEmailState(lead = {}, history = {}) {
  const email = addr(lead.email);
  if (!email) {
    const guess = addr(lead.email_guess);
    return guess && lead.email_guess_status !== "rejected" ? "candidate" : "none";
  }
  const check = lead.email_check || {};
  const mine = ["", email].includes(addr(check.email));
  const status = mine ? check.status : null;
  if (has(history.bounced, email) || status === "invalid") return "bad";
  if (has(history.delivered, email) || (mine && mailboxConfirmed(check))) return "confirmed";
  if (GUESS_SOURCES.some((p) => String(lead.email_source || "").startsWith(p))) return "candidate";
  return "domain_ready";
}

export const emailStateOf = (lead = {}) => (EMAIL_STATES[lead.email_state] || lead.email_state === "none" ? lead.email_state : computeEmailState(lead));
