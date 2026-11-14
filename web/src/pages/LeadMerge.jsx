// Merge one group of duplicate leads from the Find duplicates report: choose the lead to keep, preview in plain words,
// confirm, then a notice with Undo (24 hours). The server (app/merge_flow.py) checks every rule; nothing here is trusted.
import { useState } from "react";
import { api } from "../api.js";
import { useConfirm } from "../Dialog.jsx";

const MAX_AT_ONCE = 5;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function Preview({ p }) {
  const records = Object.entries(p.records || {});
  return (
    <div className="tip" data-testid="merge-preview">
      <p style={{ margin: "0 0 6px" }}><b>{p.summary}</b></p>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {records.length > 0 && <li>Moves across: {records.map(([k, n]) => `${n} ${k}`).join(", ")}.</li>}
        {p.filled.length > 0 && <li>Fills these empty fields from the others: {p.filled.map((f) => (f === "emails" ? "extra email addresses" : f.replace(/_/g, " "))).join(", ")}. Nothing already filled is overwritten.</li>}
        {p.tags_added.length > 0 && <li>Adds tags: {p.tags_added.join(", ")}.</li>}
        {p.phones_added.length > 0 && <li>Keeps {plural(p.phones_added.length, "extra phone number")} on the lead.</li>}
        {p.stage && <li>Stage: {p.stage.from} becomes {p.stage.to}. A closed stage is never reversed.</li>}
        {p.conflicts.length > 0 && <li>{plural(p.conflicts.length, "different value")} kept for someone to review ({p.conflicts.map((c) => c.field).join(", ")}).</li>}
        {p.compliance.length > 0 && <li>Opt-outs stay: {p.compliance.join("; ")}. The merged lead is marked do-not-contact.</li>}
      </ul>
    </div>
  );
}

export default function MergeGroup({ group, onMerged }) {
  const confirm = useConfirm();
  const richest = [...group.leads].sort((a, b) => (b.history || 0) - (a.history || 0))[0];
  const [open, setOpen] = useState(false);
  const [keep, setKeep] = useState(richest.id);
  const [preview, setPreview] = useState(null);
  const [notice, setNotice] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const others = group.leads.filter((l) => l.id !== keep).slice(0, MAX_AT_ONCE);
  const name = (l) => l.company || l.contact_name || l.id;
  const run = async (fn) => { setBusy(true); setErr(""); try { await fn(); } catch (e) { setErr(e.message); } setBusy(false); };

  const doPreview = () => run(async () => setPreview(await api.mergeDuplicates(keep, others.map((l) => l.id), true)));
  const doMerge = () => run(async () => {
    const kept = group.leads.find((l) => l.id === keep);
    const msg = `Merge ${plural(others.length, "lead")} into ${name(kept)}? ${preview.records_total} linked record${preview.records_total === 1 ? "" : "s"} will move across and the duplicates will be removed. You can undo this for 24 hours.`;
    if (!(await confirm({ title: "Merge these leads?", message: msg, confirmLabel: "Merge", danger: true }))) return;
    const r = await api.mergeDuplicates(keep, others.map((l) => l.id), false);
    setNotice({ text: `Merged ${plural(r.merged, "lead")} into ${r.keep.company}. ${r.records_total} linked record${r.records_total === 1 ? "" : "s"} moved.`, undo: r.undo_id });
    setOpen(false); setPreview(null);
    onMerged?.();
  });
  const doUndo = () => run(async () => {
    const r = await api.mergeUndo(notice.undo);
    setNotice({ text: r.notice });
    onMerged?.();
  });

  if (notice) {
    return (
      <div className="tip" role="status" data-testid="merge-notice" style={{ marginTop: 6 }}>
        {notice.text}{" "}
        {notice.undo && <button type="button" className="btn ghost" disabled={busy} onClick={doUndo} data-testid="merge-undo">Undo</button>}
        {err && <span className="error" role="alert"> {err}</span>}
      </div>
    );
  }
  if (!open) return <button type="button" className="btn ghost" style={{ marginTop: 6 }} onClick={() => setOpen(true)} data-testid="merge-open">Merge…</button>;
  return (
    <div style={{ marginTop: 8 }} data-testid="merge-panel">
      <p style={{ margin: "0 0 4px" }}><b>Which lead should stay?</b> The others are folded into it and removed.</p>
      {group.leads.map((l) => (
        <label key={l.id} style={{ display: "block", margin: "2px 0" }}>
          <input type="radio" name={`keep-${group.leads[0].id}`} checked={keep === l.id} onChange={() => { setKeep(l.id); setPreview(null); }} data-testid={`keep-${l.id}`} />{" "}
          {name(l)} <small>{l.contact_name} {l.email || l.phone} · {plural(l.history || 0, "history event")}{l.id === richest.id ? " · most history" : ""}</small>
        </label>
      ))}
      {group.size > MAX_AT_ONCE + 1 && <p className="sub">This group has {group.size} leads. {MAX_AT_ONCE} can be merged at a time; merge these, then open the report again for the rest.</p>}
      {preview && <Preview p={preview} />}
      {err && <div className="tip error" role="alert">{err}</div>}
      <div className="row" style={{ gap: 8, marginTop: 6 }}>
        <button type="button" className="btn ghost" onClick={() => { setOpen(false); setPreview(null); setErr(""); }}>Cancel</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={doPreview} data-testid="merge-preview-btn">{preview ? "Refresh preview" : "Preview"}</button>
        {preview && <button type="button" className="btn" disabled={busy} onClick={doMerge} data-testid="merge-confirm">Merge {plural(others.length, "lead")}</button>}
      </div>
    </div>
  );
}
