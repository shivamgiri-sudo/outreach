// Sales funnel settings: SLA hours, stalled days and the monthly targets by owner. Owners and workspace admins only.
import { useEffect, useState } from "react";
import { isOwner, isWorkspaceAdmin } from "../api.js";
import { sales } from "../salesApi.js";
import { inr } from "../salesFormat.js";
import Abbr from "../components/Abbr.jsx";
import { Button, Card, Field, Input } from "../ui/index.js";

export default function SalesSettings({ owners, onSaved }) {
  const editable = isOwner() || isWorkspaceAdmin();
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { sales.settings().then(setS).catch((e) => setMsg(e.message)); }, []);
  if (!s) return msg ? <div className="tip error" role="alert">{msg}</div> : null;
  const setTarget = (i, k, v) => setS({ ...s, targets: s.targets.map((t, j) => (j === i ? { ...t, [k]: v } : t)) });
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg("");
    try {
      const r = await sales.saveSettings({ sla_hours: Number(s.sla_hours), stalled_days: Number(s.stalled_days), targets: s.targets.map((t) => ({ ...t, target: Number(t.target) || 0 })) });
      setS({ ...s, ...r }); setMsg("Saved."); onSaved?.();
    } catch (err) { setMsg(err.message); }
    setBusy(false);
  };
  return (
    <section aria-labelledby="sf-h-set">
      <Card as="details" data-testid="sf-settings">
        <summary><b id="sf-h-set">Funnel settings</b> <small className="sub-inline">SLA {s.sla_hours} h · stalled after {s.stalled_days} days · {s.targets.length} target row{s.targets.length === 1 ? "" : "s"}</small></summary>
        <form onSubmit={save} className="inline-form" aria-label="Sales funnel settings">
          <div className="form-grid">
            <Field label={<>First-response <Abbr term="SLA">SLA</Abbr> (hours)</>}><Input type="number" min="1" max="720" value={s.sla_hours} disabled={!editable} onChange={(e) => setS({ ...s, sla_hours: e.target.value })} /></Field>
            <Field label="Stalled after (days without activity)"><Input type="number" min="1" max="365" value={s.stalled_days} disabled={!editable} onChange={(e) => setS({ ...s, stalled_days: e.target.value })} /></Field>
          </div>
          <b>Monthly targets (₹ of new monthly value, by owner)</b>
          {s.targets.length === 0 && <p className="sub">No targets yet. Without a target the coverage ratio shows a dash.</p>}
          {s.targets.map((t, i) => (
            <div className="sf-form-row" key={i}>
              <input type="month" aria-label="Month" value={t.month} disabled={!editable} onChange={(e) => setTarget(i, "month", e.target.value)} />
              <input list="sf-owner-list" aria-label="Owner (blank = whole team)" placeholder="Owner (blank = team)" value={t.owner} disabled={!editable} onChange={(e) => setTarget(i, "owner", e.target.value)} />
              <input type="number" min="0" aria-label="Target in rupees" value={t.target} disabled={!editable} onChange={(e) => setTarget(i, "target", e.target.value)} />
              <small className="sub-inline">{inr(Number(t.target) || 0)}</small>
              {editable && <Button variant="danger" onClick={() => setS({ ...s, targets: s.targets.filter((_, j) => j !== i) })}>Remove</Button>}
            </div>
          ))}
          <datalist id="sf-owner-list">{owners.map((o) => <option key={o} value={o} />)}</datalist>
          {editable ? (
            <div className="row">
              <Button variant="secondary" onClick={() => setS({ ...s, targets: [...s.targets, { month: new Date().toISOString().slice(0, 7), owner: "", target: 0 }] })}>Add a target</Button>
              <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save settings"}</Button>
            </div>
          ) : <p className="sub">Only the owner or a workspace admin changes these.</p>}
          {msg && <div className={msg === "Saved." ? "tip" : "tip error"} role="status">{msg}</div>}
        </form>
      </Card>
    </section>
  );
}
