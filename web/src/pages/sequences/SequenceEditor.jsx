import { useEffect, useMemo, useState } from "react";
import { api, getWorkspace, isOwner, isWorkspaceAdmin } from "../../api.js";
import StepEditor from "./StepEditor.jsx";
import { humanDay, lintStep, stepTitle } from "./lint.js";

const MAX_STEPS = 10;
const UNSUB = "\n\nNot relevant? Unsubscribe: {{unsub_link}}";
const blankStep = (day) => ({ day, name: "", subject: "", body: `{{greeting}}\n\n\n\nRegards,\n{{sender}}${UNSUB}`, variants: [] });

// What a step looks like for the chosen preview version (empty variant fields keep A's text).
const forVariant = (step, name) => {
  const v = (step.variants || []).find((x) => x.name === name);
  return v ? { subject: v.subject || step.subject, body: v.body || step.body } : { subject: step.subject, body: step.body };
};

function AbStats({ stats }) {
  const tests = (stats?.steps || []).filter((s) => s.variants.length > 1);
  if (!tests.length) return null;
  const r = stats.rule;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3 style={{ marginTop: 0 }}>A/B results</h3>
      <p className="sub">Winner rule: once every version has {r.min_sends_per_variant} real sends, the best reply rate wins if it has at least {r.min_replies} replies,
        leads by {r.min_lead_points}+ points and is {r.win_ratio}× the runner-up. After that only the winner is sent. Opens are shown, never used to decide.</p>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Step</th><th>Version</th><th>Sent</th><th>Practice</th><th>Opens</th><th>Replies</th><th>Reply rate</th></tr></thead>
          <tbody>
            {tests.flatMap((s) => s.variants.map((v) => (
              <tr key={`${s.day}-${v.name}`}>
                <td>Day {humanDay(s.day)} · {stepTitle(s.name)}</td>
                <td>{v.name} {s.winner === v.name && <span className="pill ok">Winner</span>}</td>
                <td>{v.sent}</td><td>{v.practice}</td><td>{v.opens}</td><td>{v.replies}</td><td>{v.reply_rate}%</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Edit (or view) one sequence. Saving goes to the server, which checks everything again.
export default function SequenceEditor({ sequence, onSaved, onClose, onDuplicate }) {
  const readOnly = sequence.builtin || !sequence.can_edit;
  const [draft, setDraft] = useState(() => ({ name: sequence.name || "", notes: sequence.notes || "", steps: (sequence.steps || []).map((s) => ({ ...s, variants: s.variants || [] })) }));
  // Owners pick whose sequence it is (default: the workspace chosen in the menu, else Main); admins make their own workspace's.
  const [spaces, setSpaces] = useState([]);
  const [workspace, setWorkspace] = useState(sequence.workspace_id || getWorkspace() || "main");
  useEffect(() => { if (isOwner() && !readOnly) api.workspaces().then(setSpaces).catch(() => setSpaces([])); }, [readOnly]);
  const [active, setActive] = useState({ index: 0, variant: "A" });
  const [preview, setPreview] = useState(null);
  const [stats, setStats] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (sequence.id && !sequence.builtin) api.sequenceStats(sequence.id).then(setStats).catch(() => setStats(null)); }, [sequence.id, sequence.builtin]);
  const step = draft.steps[active.index];
  const shownText = step ? forVariant(step, active.variant) : null;
  useEffect(() => {
    if (!shownText || (!shownText.subject && !shownText.body)) { setPreview(null); return undefined; }
    // Read-only views (viewers too) use the saved step's GET preview; editing previews the unsaved draft.
    const load = readOnly && sequence.id ? () => api.sequenceStepPreview(sequence.id, active.index, active.variant) : () => api.previewDraftStep(shownText);
    const timer = setTimeout(() => load().then(setPreview).catch(() => setPreview(null)), readOnly ? 0 : 400);
    return () => clearTimeout(timer);
  }, [shownText?.subject, shownText?.body, active.index, active.variant]); // eslint-disable-line react-hooks/exhaustive-deps

  const errors = useMemo(() => draft.steps.flatMap((s, i) => lintStep(s, draft.steps[i - 1]).filter((x) => x.level === "error")), [draft.steps]);
  const setStep = (i, s) => setDraft((d) => ({ ...d, steps: d.steps.map((x, j) => (j === i ? s : x)) }));
  const addStep = () => setDraft((d) => ({ ...d, steps: [...d.steps, blankStep((d.steps.at(-1)?.day ?? -3) + 3)] }));
  const removeStep = (i) => { setDraft((d) => ({ ...d, steps: d.steps.filter((_, j) => j !== i) })); setActive({ index: 0, variant: "A" }); };

  const save = async () => {
    setBusy(true); setMsg("");
    const body = { name: draft.name, notes: draft.notes, steps: draft.steps.map((s) => ({ ...s, day: Number(s.day) })), ...(isOwner() ? { workspace } : {}) };
    try {
      const saved = sequence.id ? await api.updateSequence(sequence.id, body) : await api.createSequence(body);
      setMsg(saved.new_version ? `Saved as version ${saved.version}. Leads already part-way keep the days they started on.` : "Saved.");
      onSaved(saved);
    } catch (err) { setMsg(err.message); } finally { setBusy(false); }
  };

  return (
    <section aria-label={`Sequence ${draft.name || "new"}`}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>{sequence.id ? (readOnly ? "View sequence" : "Edit sequence") : "New sequence"}{sequence.version > 1 ? ` · version ${sequence.version}` : ""}</h3>
        <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {sequence.id && (isOwner() || isWorkspaceAdmin()) && <button type="button" className="btn ghost" onClick={() => onDuplicate(sequence)}>{isOwner() || sequence.can_edit ? "Duplicate" : "Copy into my workspace"}</button>}
          {!readOnly && <button type="button" className="btn" disabled={busy || errors.length > 0} onClick={save}>{busy ? "Saving…" : "Save sequence"}</button>}
          <button type="button" className="btn ghost" onClick={onClose}>Back to list</button>
        </span>
      </div>
      {sequence.builtin && <p className="sub">The built-in sequence is read-only. Duplicate it to change the words, days or add an A/B test.</p>}
      {!sequence.builtin && readOnly && <p className="sub">{sequence.workspace_id === "main" && isWorkspaceAdmin()
        ? "Main's sequences are shared and read-only here. Use “Copy into my workspace” to make your own copy."
        : "Only an owner or an admin of this workspace can change this sequence."}</p>}
      {sequence.in_use > 0 && !readOnly && <p className="sub">{sequence.in_use} lead(s) are part-way through. Wording changes apply to their next emails; changing days saves a new version and they keep theirs.</p>}
      {msg && <p className={/saved/i.test(msg) ? "sub" : "error"} role={/saved/i.test(msg) ? "status" : "alert"}>{msg}</p>}
      {errors.length > 0 && !readOnly && <p className="error" role="alert">{errors.length} thing(s) to fix before saving (see the steps).</p>}
      <div className="split-2">
        <label>Name<input disabled={readOnly} maxLength={120} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
        <label>Who it is for (notes)<input disabled={readOnly} maxLength={500} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} /></label>
        {isOwner() && !readOnly && spaces.length > 1 && <label>Workspace<select aria-label="Workspace of this sequence" value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
          {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}
      </div>
      <AbStats stats={stats} />
      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))", alignItems: "start" }}>
        <div style={{ minWidth: 0 }}>
          {draft.steps.map((s, i) => (
            <StepEditor key={i} step={s} index={i} prev={draft.steps[i - 1]} readOnly={readOnly} canRemove={draft.steps.length > 1}
              onChange={(next) => { setStep(i, next); if (active.index !== i) setActive({ index: i, variant: "A" }); }}
              onRemove={() => removeStep(i)} previewVariant={active.index === i ? active.variant : ""}
              onPreviewVariant={(variant) => setActive({ index: i, variant })} />
          ))}
          {!readOnly && draft.steps.length < MAX_STEPS && <button type="button" className="btn ghost" onClick={addStep}>Add a step</button>}
        </div>
        <div className="card" style={{ position: "sticky", top: 12, minWidth: 0, padding: 16 }}>
          <b>Live preview · Step {active.index + 1}{step ? ` (Day ${humanDay(step.day)})` : ""} · version {active.variant}</b>
          {preview ? (
            <>
              <p className="sub" style={{ margin: "6px 0" }}>Subject: <b>{preview.subject}</b> · sample person: Alex Example, Example Retail</p>
              {preview.missing?.length > 0 && <p className="error">Not filled: {preview.missing.join(", ")}</p>}
              <iframe title="Live email preview" srcDoc={preview.html} sandbox="" style={{ width: "100%", height: "60vh", border: "1px solid var(--line)", borderRadius: 10, background: "#fff" }} />
            </>
          ) : <p className="sub">Write a subject and email to see the designed preview.</p>}
        </div>
      </div>
    </section>
  );
}
