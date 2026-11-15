import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { PageHeader, Card, CardHeader, Field, Input, Button, Badge, Switch, EmptyState, Skeleton } from "../ui/index.js";
import "../work-pages.css";

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

const OPTION_NAMES = { first_after_yes: "First message (after yes)", nudge_demo_not_opened: "Nudge: demo not opened",
  nudge_not_published: "Nudge: drafted but not published", pilot_offer: "Offer: help with existing applicants" };

export default function Workflows() {
  const [rules, setRules] = useState(null);
  const [form, setForm] = useState({ name: "", tType: "reply", tValue: "interested", aType: "whatsapp", aValue: "first_after_yes" });
  const [message, setMessage] = useState("");
  const load = () => api.workflows().then(setRules).catch((e) => { setRules([]); setMessage(e.message); });
  useEffect(() => { load(); }, []);

  const set = (patch) => setForm({ ...form, ...patch });
  const pickTrigger = (t) => set({ tType: t, tValue: TRIGGERS[t].options ? TRIGGERS[t].options[0] : "14" });
  const pickAction = (a) => set({ aType: a, aValue: ACTIONS[a].options ? ACTIONS[a].options[0] : "" });

  const owner = isOwner();                     // rules are owner-only changes on the server
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
      <PageHeader title="Simple rules"
        description={<>When something happens, do one thing. Each rule runs once per lead. WhatsApp actions still respect consent.
        For several steps, waits and branches, use the Flow builder.</>} />
      {message && <div className="tip u-mb-12" role="status">{message}</div>}

      <div className="grid g2">
        {owner && <Card>
          <CardHeader title="New rule" />
          <div className="stack">
          <Field label="Name"><Input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Yes by email → WhatsApp" /></Field>
          <Field label="When">
          <select value={form.tType} onChange={(e) => pickTrigger(e.target.value)}>
            {Object.entries(TRIGGERS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select></Field>
          {TRIGGERS[form.tType].options ? (
            <select value={form.tValue} onChange={(e) => set({ tValue: e.target.value })}>
              {TRIGGERS[form.tType].options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : <input type="number" min="1" value={form.tValue} onChange={(e) => set({ tValue: e.target.value })} />}
          <Field label="Then"><select value={form.aType} onChange={(e) => pickAction(e.target.value)}>
            {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select></Field>
          {ACTIONS[form.aType].options ? (
            <select value={form.aValue} onChange={(e) => set({ aValue: e.target.value })}>
              {ACTIONS[form.aType].options.map((o) => <option key={o} value={o}>{OPTION_NAMES[o] || o.replace(/_/g, " ")}</option>)}
            </select>
          ) : <input value={form.aValue} onChange={(e) => set({ aValue: e.target.value })} placeholder="e.g. Send pricing to {{company}} ({{first_name}})" />}
          <div className="row">
            <Button onClick={add}>Add rule</Button>
            <Button variant="secondary" onClick={() => api.starterWorkflows().then(() => { setMessage("Suggested rules added."); load(); }, (e) => setMessage(e.message))}>Add suggested rules</Button>
          </div>
          </div>
        </Card>}

        <Card>
          <CardHeader title={`Your rules (${(rules || []).length})`} actions={owner ? <Button variant="secondary" onClick={async () => { const r = await api.runTimeRules(); setMessage(`Checked time-based rules: ${r.ran} ran.`); }}>Check time-based rules now</Button> : null} />
          {rules === null && <div className="stack" aria-busy="true"><Skeleton height={48} /><Skeleton height={48} /></div>}
          {rules && rules.length === 0 && <EmptyState icon="rules" title="No rules yet">{owner ? "Add the suggested ones to start, or write your own on the left." : "The owner has not added any rules yet."}</EmptyState>}
          <div className="stack">
          {(rules || []).map((r) => (
            <div key={r.id} className="row rule-row">
              <span className="u-flex1">
                <b>{r.name}</b><br />
                <small>{describe("when", r.trigger, TRIGGERS)} → {describe("then", r.action, ACTIONS)}</small>
              </span>
              {!owner && <Badge tone={r.enabled ? "success" : "neutral"}>{r.enabled ? "On" : "Off"}</Badge>}
              {owner && <span className="row u-gap-8 u-m0">
                <Switch checked={r.enabled} showState label={`Rule ${r.name} on`} onChange={(enabled) => { setRules((all) => all.map((x) => (x.id === r.id ? { ...x, enabled } : x))); api.toggleWorkflow(r.id, enabled).catch(load); }} />
              </span>}
              {owner && <Button variant="secondary" onClick={() => api.deleteWorkflow(r.id).then(load).catch(() => load())}>Delete</Button>}
            </div>
          ))}
          </div>
        </Card>
      </div>
    </>
  );
}
