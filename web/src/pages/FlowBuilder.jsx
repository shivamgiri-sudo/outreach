import { useEffect, useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";
import FlowEditor from "./flows/FlowEditor.jsx";

const STATUS = { active: ["ok", "Active"], paused: ["warn", "Paused"], draft: ["", "Draft"] };
const TRIGGER_WORDS = {
  "trigger.manual": "Added by hand", "trigger.lead_added": "New lead", "trigger.reply": "They reply",
  "trigger.stage_changed": "Stage changes", "trigger.meeting_booked": "Meeting booked",
  "trigger.activation_event": "Product activity", "trigger.no_reply_days": "No reply for days",
  "trigger.schedule": "On a schedule", "trigger.webhook": "Webhook received",
};

// "#flows/<id>" opens one flow in the editor; "#flows" is the list.
const flowIdFromHash = () => {
  const [page, id] = window.location.hash.replace(/^#\/?/, "").split(/[/?]/);
  return page === "flows" && id ? id : null;
};

function FlowList({ onOpen }) {
  const [flows, setFlows] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const confirm = useConfirm();
  // A blank flow is saved the moment it is made, so "Untitled flow" drafts piled up with no way to remove them.
  const remove = async (f) => {
    const live = f.stats?.active_leads || 0;
    if (!(await confirm({ message: `Delete “${f.name}”?${live ? ` ${live} lead(s) still in it will be stopped.` : ""} This cannot be undone.`,
      confirmLabel: "Delete flow", danger: true }))) return;
    try { await api.deleteFlow(f.id); setFlows((rows) => rows.filter((x) => x.id !== f.id)); } catch (e) { setError(e.message); }
  };
  useEffect(() => {
    api.flows().then(setFlows).catch((e) => { setError(e.message); setFlows([]); });
    api.flowTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);
  const importFile = async (file) => {
    if (!file) return;
    setError("");
    try {
      if (file.size > 500_000) throw new Error("That file is too big to be a flow.");
      onOpen((await api.importFlow(JSON.parse(await file.text()))).id);
    } catch (e) { setError(e instanceof SyntaxError ? "That file is not a flow (it is not valid JSON)." : e.message); }
  };
  const create = async (make) => {
    setError("");
    try { onOpen((await make()).id); } catch (e) { setError(e.message); }
  };
  return (
    <>
      <h2>Flow builder</h2>
      <p className="sub">Draw your own communication flow: a trigger, then checks, waits and messages across email, WhatsApp, SMS,
        LinkedIn tasks, calls and meetings. Every message keeps the app's rules: consent before WhatsApp, practice mode, caps,
        business hours and the do-not-contact list.</p>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="row" style={{ marginBottom: 16 }}>
        <button type="button" className="btn" onClick={() => setPicking(!picking)} aria-expanded={picking}>New from template</button>
        <button type="button" className="btn ghost" onClick={() => create(() => api.createFlow({ name: "Untitled flow" }))}>Blank flow</button>
        <label className="btn ghost" style={{ cursor: "pointer" }}>Import a flow
          <input type="file" accept="application/json,.json" hidden onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} /></label>
      </div>
      {picking && (
        <div className="grid g2" style={{ marginBottom: 16 }} data-testid="flow-templates">
          {templates.map((t) => (
            <div key={t.key} className="card">
              <h3 style={{ marginTop: 0 }}>{t.name}</h3>
              <p className="sub" style={{ marginBottom: 12 }}>{t.description}</p>
              <button type="button" className="btn" onClick={() => create(() => api.flowFromTemplate(t.key))} aria-label={`Use template: ${t.name}`}>
                Use this template
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="card table-card">
        {flows === null ? <p className="empty">Loading…</p> : flows.length === 0 ? (
          <p className="empty">No flows yet. Start from a template: the cold email one is the same as today's sequence.</p>
        ) : (
          <table className="stack-table">
            <thead><tr><th>Flow</th><th>Status</th><th>Starts when</th><th>Active leads</th><th>Last edited</th><th /></tr></thead>
            <tbody>
              {flows.map((f) => {
                const [pill, word] = STATUS[f.status] || ["", f.status];
                return (
                  <tr key={f.id}>
                    <td data-label="Flow"><b>{f.name}</b><br /><small className="sub-inline">{f.steps} step{f.steps === 1 ? "" : "s"} · v{f.version}</small></td>
                    <td data-label="Status"><span className={`pill ${pill}`}>{word}</span></td>
                    <td data-label="Starts when">{TRIGGER_WORDS[f.trigger] || "No trigger yet"}</td>
                    <td data-label="Active leads">{f.stats.active_leads} <small className="sub-inline">of {f.stats.total}</small></td>
                    <td data-label="Last edited">{istLabel(f.updated_at)}</td>
                    <td className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <button type="button" className="btn ghost" onClick={() => onOpen(f.id)} aria-label={`Open ${f.name}`}>Open</button>
                      <button type="button" className="btn ghost danger" onClick={() => remove(f)} aria-label={`Delete ${f.name}`}>Delete</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

export default function FlowBuilder() {
  const [flowId, setFlowId] = useState(flowIdFromHash);
  const [catalog, setCatalog] = useState(null);
  useEffect(() => {
    const onHash = () => setFlowId(flowIdFromHash());
    window.addEventListener("hashchange", onHash);
    api.flowCatalog().then(setCatalog).catch(() => setCatalog(null));
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const open = (id) => { window.location.hash = id ? `flows/${id}` : "flows"; setFlowId(id); };
  return flowId
    ? <FlowEditor key={flowId} flowId={flowId} catalog={catalog} onBack={() => open(null)} />
    : <FlowList onOpen={open} />;
}
