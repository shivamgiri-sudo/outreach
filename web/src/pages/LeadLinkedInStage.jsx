import { useState } from "react";
import { api, canWrite } from "../api.js";
import { istLabel } from "../time.js";
import { accountName, forgetLinkedinAccounts, useLinkedinAccounts } from "./LinkedInAccountPick.jsx";

// The Leads list's LinkedIn stage filter values (li_stage_filter from GET /api/leads; "messaging" = any "message N sent").
export const LI_STAGES = [
  ["not_contacted", "Has LinkedIn link, not contacted"], ["queued", "Request queued"], ["waiting_accept", "Waiting to accept"],
  ["accepted", "Accepted, message 1 not sent"], ["messaging", "Messages being sent"], ["replied", "Replied"],
  ["finished", "Finished"], ["stopped", "Stopped"],
];

export function liStageMatches(lead, value) {
  if (!value) return true;
  if (value === "__none") return !lead.li_stage;
  return lead.li_stage_filter === value;
}

export function LinkedInStageFilter({ value, onChange }) {
  return (
    <select aria-label="Filter by LinkedIn stage" value={value} onChange={(e) => onChange(e.target.value)} data-testid="li-stage-filter">
      <option value="">Any LinkedIn stage</option>
      {LI_STAGES.map(([k, v]) => <option key={k} value={k}>LinkedIn: {v}</option>)}
      <option value="__none">No LinkedIn link</option>
    </select>
  );
}

// Table cell: the LinkedIn stage, the account that sends (or will send) and the next due step.
export function LinkedInCell({ lead }) {
  const { accounts } = useLinkedinAccounts();
  if (!lead.li_stage) return <small className="sub">—</small>;
  return (
    <>
      <small>{lead.li_stage_label}</small>
      <br /><small className="sub">{lead.li_account ? accountName(accounts, lead.li_account) : "any account"}</small>
      {lead.li_next_due && <><br /><small className={new Date(lead.li_next_due) < new Date() ? "pill err" : ""}>due {istLabel(lead.li_next_due)}</small></>}
    </>
  );
}

// Bulk bar: send the picked leads from one LinkedIn account (people already contacted stay with their account).
export function BulkLinkedInAccount({ picked, onDone }) {
  const { accounts } = useLinkedinAccounts();
  const [busy, setBusy] = useState(false);
  if (!canWrite() || !accounts.length) return null;
  const run = async (value) => {
    setBusy(true);
    try {
      const r = await api.assignLinkedinAccount(picked, value === "__any" ? "" : value);
      const left = r.reasons.map((x) => `${x.count} ${x.reason}`).join("; ");
      forgetLinkedinAccounts();
      onDone(`LinkedIn account set for ${r.ok} of ${r.asked} lead(s).${left ? ` Left out: ${left}.` : ""}`, r.ok > 0);
    } catch (e) { onDone(e.message, false); }
    setBusy(false);
  };
  return (
    <select aria-label="Send selected leads from LinkedIn account" value="" disabled={busy} style={{ width: "auto" }}
      onChange={(e) => e.target.value && run(e.target.value)}>
      <option value="">Send from LinkedIn account…</option>
      {accounts.map((a) => <option key={a.account} value={a.account}>{accountName(accounts, a.account)}</option>)}
      <option value="__any">Any account (clear the pick)</option>
    </select>
  );
}
