import { useEffect, useState } from "react";
import { api } from "../api.js";

const TRIGGERS = {
  reply: { label: "They reply, and it sounds…", options: ["interested", "question", "not_now", "wrong_person", "unsubscribe", "other"] },
  stage_change: { label: "Their stage becomes…", options: ["verified", "enriched", "contacted", "engaged", "interested", "whatsapp_active", "call_done", "trial", "customer", "not_now"] },
  activation: { label: "In the product they…", options: ["demo_opened", "role_drafted", "role_published", "candidate_screened", "interview_booked", "converted"] },
  no_reply_days: { label: "We emailed and heard nothing for (days)…", options: null },
};
const ACTIONS = {
  whatsapp: { label: "Send WhatsApp template", options: ["first_after_yes", "nudge_demo_not_opened", "nudge_not_published", "pilot_offer"] },
  task: { label: "Create a task for me", options: null },
  set_stage: { label: "Move them to stage", options: ["not_now", "engaged", "disqualified", "call_done"] },
  note: { label: "Add a note to their history", options: null },
};

const describe = (kind, step, defs) => `${defs[step.type].label} ${step.value}`;

export default function Workflows() {
  const [rules, setRules] = useState([]);
  const [form, setForm] = useState({ name: "", tType: "reply", tValue: "interested", aType: "whatsapp", aValue: "first_after_yes" });
  const [message, setMessage] = useState("");
  const load = () => api.workflows().then(setRules);
  useEffect(() => { load(); }, []);

  const set = (patch) => setForm({ ...form, ...patch });
  const pickTrigger = (t) => set({ tType: t, tValue: TRIGGERS[t].options ? TRIGGERS[t].options[0] : "14" });
  const pickAction = (a) => set({ aType: a, aValue: ACTIONS[a].options ? ACTIONS[a].options[0] : "" });

  const add = async () => {
    try {
      await api.addWorkflow({
        name: form.name,
        trigger: { type: form.tType, value: form.tType === "no_reply_days" ? Number(form.tValue) : form.tValue },
        action: { type: form.aType, value: form.aValue },
      });
      setForm({ ...form, name: "" });
      setMessage("Rule added.");
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  return (
    <>
      <h2>Simple rules</h2>
      <p className="sub">When something happens, do one thing. Each rule runs once per lead. WhatsApp actions still respect consent.
        For several steps, waits and branches, use the Flow builder.</p>
      {message && <div className="tip" style={{ marginBottom: 12 }}>{message}</div>}

      <div className="grid g2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>New rule</h3>
          <label>Name</label>
          <input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Yes by email → WhatsApp" />
          <label>When</label>
          <select value={form.tType} onChange={(e) => pickTrigger(e.target.value)}>
            {Object.entries(TRIGGERS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          {TRIGGERS[form.tType].options ? (
            <select style={{ marginTop: 6 }} value={form.tValue} onChange={(e) => set({ tValue: e.target.value })}>
              {TRIGGERS[form.tType].options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : <input style={{ marginTop: 6 }} type="number" min="1" value={form.tValue} onChange={(e) => set({ tValue: e.target.value })} />}
          <label>Then</label>
          <select value={form.aType} onChange={(e) => pickAction(e.target.value)}>
            {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          {ACTIONS[form.aType].options ? (
            <select style={{ marginTop: 6 }} value={form.aValue} onChange={(e) => set({ aValue: e.target.value })}>
              {ACTIONS[form.aType].options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : <input style={{ marginTop: 6 }} value={form.aValue} onChange={(e) => set({ aValue: e.target.value })} placeholder="Text" />}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={add}>Add rule</button>
            <button className="btn ghost" onClick={async () => { await api.starterWorkflows(); setMessage("Suggested rules added."); load(); }}>Add suggested rules</button>
          </div>
        </div>

        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>Your rules ({rules.length})</h3>
            <button className="btn ghost" onClick={async () => { const r = await api.runTimeRules(); setMessage(`Checked time-based rules: ${r.ran} ran.`); }}>Check time-based rules now</button>
          </div>
          {rules.length === 0 && <p className="empty">No rules yet. Add the suggested ones to start.</p>}
          {rules.map((r) => (
            <div key={r.id} className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
              <span style={{ flex: 1 }}>
                <b>{r.name}</b><br />
                <small>{describe("when", r.trigger, TRIGGERS)} → {describe("then", r.action, ACTIONS)}</small>
              </span>
              <label className="row" style={{ fontWeight: 400, margin: 0 }}>
                <input type="checkbox" style={{ width: "auto" }} checked={r.enabled} onChange={(e) => { const enabled = e.target.checked; setRules((all) => all.map((x) => (x.id === r.id ? { ...x, enabled } : x))); api.toggleWorkflow(r.id, enabled).catch(load); }} />
                On
              </label>
              <button className="btn ghost" onClick={() => api.deleteWorkflow(r.id).then(load).catch(() => load())}>Delete</button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
