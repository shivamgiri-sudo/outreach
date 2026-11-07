import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";

// Saved Leads filters, per person (kept on the server). Pick one to apply it; save the current filters by name.
export default function LeadViews({ filters, onApply }) {
  const [views, setViews] = useState([]);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState("");
  const [error, setError] = useState("");
  const canSave = canWrite();       // viewers are read-only, so they can't save views
  const load = () => api.leadViews().then(setViews).catch(() => {});
  useEffect(() => { load(); }, []);

  const pick = (id) => {
    setPicked(id);
    const view = views.find((v) => v.id === id);
    if (view) onApply(view.filters);
  };
  const save = async (e) => {
    e.preventDefault();
    setError("");
    try { const v = await api.saveLeadView(name, filters); setNaming(false); setName(""); await load(); setPicked(v.id); }
    catch (err) { setError(err.message); }
  };
  const remove = async () => {
    if (!picked) return;
    try { await api.deleteLeadView(picked); setPicked(""); load(); } catch (err) { setError(err.message); }
  };

  return (
    <span className="row" style={{ gap: 6, flexWrap: "wrap" }} data-testid="lead-views">
      <select aria-label="Saved views" value={picked} onChange={(e) => pick(e.target.value)} style={{ width: "auto" }}>
        <option value="">{views.length ? "Saved views…" : "No saved views"}</option>
        {views.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>
      {picked && canSave && <button type="button" className="linkish" onClick={remove}>Delete view</button>}
      {!canSave ? null : naming ? (
        <form className="row" style={{ gap: 4 }} onSubmit={save}>
          <input aria-label="View name" autoFocus maxLength={60} placeholder="e.g. My hot Noida leads" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 190 }} />
          <button className="btn ghost" type="submit" disabled={!name.trim()}>Save</button>
          <button className="btn ghost" type="button" onClick={() => setNaming(false)}>Cancel</button>
        </form>
      ) : <button type="button" className="btn ghost" onClick={() => setNaming(true)}>Save view</button>}
      {error && <small className="tip error" role="alert">{error}</small>}
    </span>
  );
}
