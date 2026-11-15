import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";

// Who works this lead. Choices come from the team list and the logins; every change is logged on the lead.
export function LeadOwner({ lead, onChanged }) {
  const [owners, setOwners] = useState([]);
  const [error, setError] = useState("");
  const canEdit = canWrite();
  useEffect(() => { api.leadOwners().then((r) => setOwners(r.owners)).catch(() => {}); }, []);
  const choices = lead.owner && !owners.includes(lead.owner) ? [lead.owner, ...owners] : owners;
  const change = async (owner) => {
    setError("");
    try { onChanged(await api.setLeadOwner(lead.id, owner)); } catch (e) { setError(e.message); }
  };
  return (
    <div className="row u-m-8-0 u-wrap" data-testid="lead-owner">
      <label htmlFor={`owner-${lead.id}`} className="u-m0"><b>Owner</b></label>
      {canEdit ? (
        <select id={`owner-${lead.id}`} value={lead.owner || ""} onChange={(e) => change(e.target.value)} className="u-w-auto u-minwidth-160">
          <option value="">Nobody yet</option>
          {choices.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      ) : <span>{lead.owner || "Nobody yet"}</span>}
      {error && <small className="tip error" role="alert">{error}</small>}
    </div>
  );
}

// Free-text labels (e.g. "Noida BPO", "VIP"); used by the Leads tag filter, exports and flows.
export function LeadTags({ lead, onChanged }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const canEdit = canWrite();
  const tags = lead.tags || [];
  const run = async (add, remove) => {
    setError("");
    try { onChanged(await api.changeLeadTags(lead.id, add, remove)); setText(""); } catch (e) { setError(e.message); }
  };
  const submit = (e) => { e.preventDefault(); if (text.trim()) run([text.trim()], []); };
  return (
    <div className="u-m-8-0" data-testid="lead-tags">
      <div className="row u-wrap u-gap-8">
        <b>Tags</b>
        {tags.length === 0 && <small className="sub u-m0">none</small>}
        {tags.map((t) => (
          <span key={t} className="pill rec">
            {t}
            {canEdit && <button type="button" className="linkish u-ml-8" aria-label={`Remove tag ${t}`} onClick={() => run([], [t])}>✕</button>}
          </span>
        ))}
      </div>
      {canEdit && (
        <form className="row u-mt-8" onSubmit={submit} >
          <input aria-label="New tag" placeholder="Add a tag, e.g. Noida BPO" maxLength={40} value={text} onChange={(e) => setText(e.target.value)} className="u-flex1 u-minwidth-160 u-w-auto" />
          <button className="btn ghost" type="submit" disabled={!text.trim()}>Add tag</button>
        </form>
      )}
      {error && <p className="tip error" role="alert">{error}</p>}
    </div>
  );
}

export default function LeadAssign({ lead, onChanged }) {
  return (
    <div className="card tight">
      <LeadOwner lead={lead} onChanged={onChanged} />
      <LeadTags lead={lead} onChanged={onChanged} />
    </div>
  );
}
