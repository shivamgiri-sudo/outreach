import { useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { STAGE_LABELS, stageLabel } from "../labels.js";
import { useConfirm } from "../Dialog.jsx";

// After a bulk change: what happened (changed / left out and why) and, when it can be reversed, an Undo button.
export function BulkNotice({ notice, onUndone, onClose }) {
  const [busy, setBusy] = useState(false);
  const [undone, setUndone] = useState("");
  if (!notice) return null;
  const undo = async () => {
    setBusy(true);
    try {
      const r = await api.bulkUndo(notice.undoId);
      const left = (r.reasons || []).map((x) => `${x.count} ${x.reason}`).join("; ");
      setUndone(`Undone: ${r.restored} restored, ${r.skipped} left as they are${left ? ` (${left})` : ""}.`);
      onUndone?.();
    } catch (e) { setUndone(e.message); }
    setBusy(false);
  };
  return (
    <div className="tip" role="status" style={{ marginBottom: 12 }} data-testid="bulk-notice">
      {undone || notice.text}{" "}
      {notice.undoId && !undone && <button className="btn ghost" disabled={busy} onClick={undo} data-testid="bulk-undo">{busy ? "Undoing…" : "Undo"}</button>}{" "}
      <button className="linkish" aria-label="Dismiss message" onClick={onClose}>✕</button>
      {notice.undoId && !undone && <span className="sub" style={{ display: "block", margin: "4px 0 0" }}>Undo works for 24 hours and only on leads nobody has changed since.</span>}
    </div>
  );
}

const reasonLine = (r) => (r.reasons || []).map((x) => `${x.count} ${x.reason}`).join("; ");

// Leaver handover: "give everything <person> owns to <person>". Dry run first, then a confirm that states the count.
export function HandoverPanel({ owners, onDone }) {
  const confirm = useConfirm();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [stage, setStage] = useState("");
  const [untouched, setUntouched] = useState(false);
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!(isOwner() || isWorkspaceAdmin())) return null;
  const body = (dry) => ({ from_owner: from, to_owner: to, stage, only_untouched: untouched, dry_run: dry });
  const reset = () => { setPlan(null); setErr(""); };

  const check = async () => {
    setBusy(true); reset();
    try { setPlan(await api.bulkReassign(body(true))); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const run = async () => {
    const n = plan.will_move;
    const ok = await confirm({ title: `Reassign ${n} lead${n === 1 ? "" : "s"} from ${plan.from_owner} to ${plan.to_owner}?`,
      message: `${plan.from_owner} loses access to them at once if lead ownership is on. Running email and LinkedIn steps carry on under ${plan.to_owner}. You can undo this for 24 hours.`, confirmLabel: `Yes, reassign ${n}` });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api.bulkReassign(body(false));
      const left = reasonLine(r);
      onDone({ text: `Reassigned ${r.moved} lead(s) from ${r.from_owner} to ${r.to_owner}.${left ? ` Left out: ${left}.` : ""}`, undoId: r.undo_id }, r.moved > 0);
      reset();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <details className="card" style={{ padding: "8px 12px", marginBottom: 12 }} data-testid="handover-panel">
      <summary style={{ cursor: "pointer" }}><b>Hand over a person’s leads</b> <span className="sub">(someone is leaving or changing role)</span></summary>
      <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <select aria-label="Take leads from" value={from} onChange={(e) => { setFrom(e.target.value); reset(); }} style={{ width: "auto" }}>
          <option value="">Leads of…</option>{owners.map((n) => <option key={n} value={n}>{n}</option>)}</select>
        <select aria-label="Give leads to" value={to} onChange={(e) => { setTo(e.target.value); reset(); }} style={{ width: "auto" }}>
          <option value="">Give to…</option>{owners.filter((n) => n !== from).map((n) => <option key={n} value={n}>{n}</option>)}</select>
        <select aria-label="Only this stage" value={stage} onChange={(e) => { setStage(e.target.value); reset(); }} style={{ width: "auto" }}>
          <option value="">Every stage</option>{Object.keys(STAGE_LABELS).map((k) => <option key={k} value={k}>{stageLabel(k)}</option>)}</select>
        <label className="check-row" style={{ margin: 0 }}><input type="checkbox" checked={untouched} onChange={(e) => { setUntouched(e.target.checked); reset(); }} /> Only leads not yet contacted</label>
        <button className="btn ghost" disabled={!from || !to || busy} onClick={check}>{busy && !plan ? "Checking…" : "Check how many"}</button>
      </div>
      {err && <p className="sub" role="alert" style={{ color: "var(--danger, #b42318)" }}>{err}</p>}
      {plan && (
        <div style={{ marginTop: 8 }} data-testid="handover-plan">
          <p style={{ margin: "0 0 6px" }}><b>{plan.will_move}</b> lead(s) would move from <b>{plan.from_owner}</b> to <b>{plan.to_owner}</b>
            {plan.skipped > 0 && <>; {plan.skipped} left out ({reasonLine(plan)})</>}. Nothing has changed yet.</p>
          {plan.sample.length > 0 && <p className="sub" style={{ margin: "0 0 6px" }}>For example: {plan.sample.map((s) => s.company).join(", ")}{plan.will_move > plan.sample.length ? " …" : ""}</p>}
          <button className="btn" disabled={busy || plan.will_move === 0} onClick={run}>Reassign {plan.will_move}…</button>
        </div>
      )}
    </details>
  );
}
