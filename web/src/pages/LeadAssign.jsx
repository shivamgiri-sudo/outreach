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
    <div className="row" style={{ margin: "6px 0", flexWrap: "wrap" }} data-testid="lead-owner">
      <label htmlFor={`owner-${lead.id}`} style={{ margin: 0 }}><b>Owner</b></label>
      {canEdit ? (
        <select id={`owner-${lead.id}`} value={lead.owner || ""} onChange={(e) => change(e.target.value)} style={{ width: "auto", minWidth: 160 }}>
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
    <div style={{ margin: "6px 0" }} data-testid="lead-tags">
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        <b>Tags</b>
        {tags.length === 0 && <small className="sub" style={{ margin: 0 }}>none</small>}
        {tags.map((t) => (
          <span key={t} className="pill rec">
            {t}
            {canEdit && <button type="button" className="linkish" aria-label={`Remove tag ${t}`} style={{ marginLeft: 6 }} onClick={() => run([], [t])}>✕</button>}
          </span>
        ))}
      </div>
      {canEdit && (
        <form className="row" onSubmit={submit} style={{ marginTop: 6 }}>
          <input aria-label="New tag" placeholder="Add a tag, e.g. Noida BPO" maxLength={40} value={text} onChange={(e) => setText(e.target.value)} style={{ flex: "1 1 160px", width: "auto" }} />
          <button className="btn ghost" type="submit" disabled={!text.trim()}>Add tag</button>
        </form>
      )}
      {error && <p className="tip error" role="alert">{error}</p>}
    </div>
  );
}

export default function LeadAssign({ lead, onChanged }) {
  return (
    <div className="card" style={{ margin: "10px 0", padding: "10px 14px" }}>
      <LeadOwner lead={lead} onChanged={onChanged} />
      <LeadTags lead={lead} onChanged={onChanged} />
    </div>
  );
}
