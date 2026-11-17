import { EMAIL_STATES, emailStateOf } from "../emailState.js";
import { Badge } from "../ui/index.js";

// The small "how sure is this email" badge next to an address (Leads table, lead drawer). Tooltip in plain words.
export default function EmailStateBadge({ lead }) {
  const state = emailStateOf(lead);
  const info = EMAIL_STATES[state];
  if (!info) return null;
  const tip = lead.email_state_why || info.tip;
  return <Badge tone={info.tone} className="email-state" title={tip} aria-label={`${info.label}: ${tip}`} data-testid="email-state" data-state={state}>{info.label}</Badge>;
}
