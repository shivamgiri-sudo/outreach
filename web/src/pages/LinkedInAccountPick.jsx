import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";

// Known LinkedIn accounts and owner defaults, read once per page load (they change rarely).
let cache = null;
export function useLinkedinAccounts() {
  const [data, setData] = useState(cache || { accounts: [], owner_defaults: {} });
  useEffect(() => {
    if (cache) return;
    api.linkedinAccounts().then((d) => { cache = d; setData(d); }).catch(() => {});
  }, []);
  return data;
}
export const forgetLinkedinAccounts = () => { cache = null; };

export const accountName = (accounts, id) => {
  const a = (accounts || []).find((x) => x.account === id);
  return a ? (a.name ? `${a.name} (${a.account})` : a.account) : id;
};

// Mirrors app/linkedin_accounts.assigned: taken by an account, else picked for the lead, else the owner's default.
export function leadAccount(lead, defaults = {}) {
  const taken = lead?.linkedin_seq?.account;
  if (taken) return [taken, "taken"];
  if (lead?.linkedin_account) return [lead.linkedin_account, "lead"];
  const owner = String(lead?.owner || "").trim().toLowerCase();
  const hit = Object.entries(defaults || {}).find(([name]) => name.trim().toLowerCase() === owner);
  return owner && hit ? [hit[1], "owner"] : ["", ""];
}

const sentStep1 = (seq) => Boolean(seq && ((seq.done || []).includes(1) || seq.accepted_at || seq.last_sent_at
  || ["waiting_for_acceptance", "messaging", "finished"].includes(seq.status)));

// Lead drawer: "Send from LinkedIn account". Locked once the connection request went out (a person never moves).
export default function LinkedInAccountPick({ lead, onChanged }) {
  const { accounts, owner_defaults: defaults } = useLinkedinAccounts();
  const [msg, setMsg] = useState("");
  const [current, how] = leadAccount(lead, defaults);
  if (!accounts.length && !current) return null;
  const locked = sentStep1(lead.linkedin_seq);
  const change = async (value) => {
    try {
      const r = await api.setLeadLinkedinAccount(lead.id, value);
      setMsg(r.changed ? "Saved." : "No change.");
      onChanged?.();
    } catch (e) { setMsg(e.message); }
  };
  const why = how === "owner" ? ` (default for ${lead.owner})` : how === "taken" ? " (this account has taken the person)" : "";
  return (
    <div className="row" style={{ alignItems: "center", gap: 8, marginBottom: 8 }} data-testid="linkedin-account-pick">
      <label style={{ margin: 0 }}>Send from LinkedIn account
        <select aria-label="Send from LinkedIn account" value={lead.linkedin_account || ""} disabled={!canWrite() || locked}
          onChange={(e) => change(e.target.value)} style={{ width: "auto", marginLeft: 6 }}>
          <option value="">{how === "owner" ? `Owner's default (${accountName(accounts, current)})` : "Any account (first to take them)"}</option>
          {accounts.map((a) => <option key={a.account} value={a.account}>{accountName(accounts, a.account)}</option>)}
        </select>
      </label>
      <small className="sub" style={{ margin: 0 }}>
        {current ? `Now: ${accountName(accounts, current)}${why}.` : "Now: any account."}
        {locked && " The connection request was sent, so the person stays with this account."}
      </small>
      {msg && <small role="status">{msg}</small>}
    </div>
  );
}
