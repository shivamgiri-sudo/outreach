import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { Button, Input } from "../ui/index.js";

// Saved Leads filters, per person (kept on the server): a segmented row of the saved views, "Save view" for the current filters.
// A visually hidden <select aria-label="Saved views"> stays as the accessible and scriptable alternative to the row.
export default function LeadViews({ filters, onApply, onClear }) {
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
    <div className="lead-views" data-testid="lead-views">
      <select className="sr-only" aria-label="Saved views" value={picked} onChange={(e) => pick(e.target.value)}>
        <option value="">{views.length ? "Saved views…" : "No saved views"}</option>
        {views.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>
      {views.length > 0 && (
        <div className="seg" role="group" aria-label="Saved views list">
          <button type="button" className={picked ? "" : "on"} aria-pressed={!picked} onClick={() => { setPicked(""); onClear?.(); }}>All leads</button>
          {views.map((v) => <button key={v.id} type="button" className={picked === v.id ? "on" : ""} aria-pressed={picked === v.id} onClick={() => pick(v.id)}>{v.name}</button>)}
        </div>
      )}
      {picked && canSave && <button type="button" className="linkish" onClick={remove}>Delete view</button>}
      {!canSave ? null : naming ? (
        <form className="row lead-views-form" onSubmit={save}>
          <Input aria-label="View name" autoFocus maxLength={60} placeholder="e.g. My hot Noida leads" value={name} onChange={(e) => setName(e.target.value)} />
          <Button variant="secondary" size="sm" type="submit" disabled={!name.trim()}>Save</Button>
          <Button variant="ghost" size="sm" onClick={() => setNaming(false)}>Cancel</Button>
        </form>
      ) : <Button variant="ghost" size="sm" icon="plus" onClick={() => setNaming(true)}>Save view</Button>}
      {error && <small className="tip error" role="alert">{error}</small>}
    </div>
  );
}
