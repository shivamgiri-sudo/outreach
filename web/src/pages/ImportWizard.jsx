import { useEffect, useState } from "react";
import { istLabel } from "../time.js";
import { api, canWrite } from "../api.js";
import { sourceLabel, sourceOptions, useSourceRegistry } from "../labels.js";

// Four steps: add the list, check the columns, dry run (nothing saved), import (with undo).
const STEPS = ["Add your list", "Check the columns", "Dry run", "Done"];
const WARNING_LABELS = {
  invalid_email: "Emails that are not valid (left out)", typo_fixed: "Email typos fixed", invalid_phone: "Phones that could not be read",
  junk_phone: "Placeholder phone numbers", landline_without_std: "Landlines without an STD code", invalid_website: "Websites that are not valid",
  free_mail_domain: "Personal email providers given as a website", not_company_site: "Social or listing pages given as a website",
  role_address: "Shared inboxes (info@, hr@…)", role_address_skipped: "Shared inboxes left out", free_mail: "Personal addresses (Gmail, Yahoo…)",
  free_mail_skipped: "Personal addresses left out", foreign_phone: "Numbers outside India (kept)", suppressed: "On the do-not-contact list (row skipped)",
  suppressed_extra: "Extra addresses on the do-not-contact list", closed_lead: "Matches leads that are closed (their stage stays)",
  source_opt_out: "Opted out of email in the source file (row skipped)", do_not_call: "Marked do not call (phones left out)",
  email_flagged: "Emails the source marked risky or unverified (check before emailing)",
  custom_unreadable: "Custom field values that could not be read",
};

const ACTIONS = { create: "New lead", merge: "Adds details to a lead you have", duplicate: "Same as an earlier row",
  unchanged: "Already here, nothing new", rejected: "Left out", skipped: "Skipped (do not contact)" };

// A leading = + - @ would run as a formula in Excel or Sheets; prefix it with ' so it stays plain text.
const safeCell = (v) => { const t = String(v ?? ""); return `"${(/^[=+\-@\t\r]/.test(t) ? "'" + t : t).replace(/"/g, '""')}"`; };

function downloadLeftOut(columns, rows) {
  const headers = ["Row", "Why it was left out", ...columns.map((c) => c.column)];
  const lines = rows.map((r) => [r.row, r.reason, ...columns.map((c) => r.values?.[c.column])].map(safeCell).join(","));
  const blob = new Blob([[headers.map(safeCell).join(","), ...lines].join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "left-out-rows.csv"; a.click();
  URL.revokeObjectURL(a.href);
}

function sureness(col) {
  if (!col.field) return <span className="pill">Not imported</span>;
  if (col.confidence >= 0.9) return <span className="pill ok">Sure</span>;
  return <span className="pill warn">{col.confidence >= 0.7 ? "Likely" : "A guess"}</span>;
}

function Kpi({ n, label }) {
  return <div className="kpi"><b>{n}</b><span>{label}</span></div>;
}

function History({ onUndo }) {
  const [items, setItems] = useState([]);
  useEffect(() => { api.importHistory().then((h) => setItems(h.slice(0, 5))).catch(() => {}); }, []);
  if (!items.length) return null;
  return (
    <details className="section">
      <summary>Recent imports</summary>
      {items.map((h) => (
        <div key={h.id} className="row" style={{ justifyContent: "space-between", padding: "6px 0" }}>
          <span>{istLabel(h.at)} · {h.format} · {h.created} new, {h.merged} updated</span>
          {h.undone_at ? <span className="pill">Undone</span>
            : <button className="btn ghost" onClick={() => onUndo(h.id).then((ok) => ok && setItems((xs) => xs.map((x) => x.id === h.id ? { ...x, undone_at: "now" } : x)))}>Undo</button>}
        </div>
      ))}
    </details>
  );
}

function MappingStep({ preview, mapping, setMapping, remember, setRemember }) {
  const fields = Object.entries(preview.fields);
  return (
    <>
      <div className="tip" style={{ marginBottom: 12 }}>
        This looks like: <b>{preview.format.name}</b>.
        {preview.format.saved_mapping && " Your saved column choices for this layout are filled in."}
        {" "}Saved as source “{sourceLabel(preview.source)}”.
      </div>
      <table className="map-table">
        <thead><tr><th>Column in your file</th><th>Sample values</th><th>Goes to</th><th>Match</th></tr></thead>
        <tbody>
          {preview.columns.map((c) => {
            const chosen = mapping[c.column] ?? (c.field || "ignore");
            return (
              <tr key={c.column}>
                <td><b>{c.column}</b></td>
                <td className="samples">{c.samples.length ? c.samples.join(" · ") : "—"}</td>
                <td>
                  <select aria-label={`Field for ${c.column}`} value={chosen} onChange={(e) => setMapping({ ...mapping, [c.column]: e.target.value })}>
                    <option value="ignore">Don't import</option>
                    {fields.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                  </select>
                </td>
                <td>{mapping[c.column] ? <span className="pill ok">Your choice</span> : sureness(c)}<br /><small className="samples">{c.how}</small></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <label className="check-row">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember these choices for the next file like this
      </label>
    </>
  );
}

function DryRun({ preview, options, setOptions }) {
  const s = preview.summary;
  const leftOut = [...preview.rejected, ...preview.skipped].sort((a, b) => a.row - b.row);
  const warnings = Object.entries(s.warnings || {});
  return (
    <>
      <p className="sub" style={{ margin: "0 0 8px" }}>Nothing has been saved yet. This is what importing would do with {s.rows} rows.</p>
      <div className="kpi-row">
        <Kpi n={s.create} label="new leads" /><Kpi n={s.merge} label="existing leads get new details" />
        <Kpi n={s.duplicates_in_file} label="duplicates inside this file" /><Kpi n={s.unchanged} label="already here, nothing new" />
        <Kpi n={leftOut.length} label="left out" />
      </div>
      <div>
        <label className="check-row">
          <input type="checkbox" checked={options.skip_role_addresses} onChange={(e) => setOptions({ ...options, skip_role_addresses: e.target.checked })} />
          Leave out shared inboxes (info@, hr@…)
        </label>
        <label className="check-row">
          <input type="checkbox" checked={options.skip_free_mail} onChange={(e) => setOptions({ ...options, skip_free_mail: e.target.checked })} />
          Leave out personal addresses (Gmail, Yahoo…)
        </label>
      </div>
      {preview.merges.length > 0 && (
        <details className="section" open>
          <summary>Added to leads you already have ({s.merge})</summary>
          {preview.merges.slice(0, 50).map((m) => (
            <p key={m.row} style={{ margin: "6px 0" }}>
              Row {m.row} → <b>{m.company || "a lead"}</b> <span className="sub-inline">({m.matched_by})</span>:{" "}
              {m.filled.length ? `fills ${m.filled.join(", ")}` : "no empty fields to fill"}
              {m.added_contact && `; adds ${m.added_contact} as another contact`}
              {m.conflicts.map((c) => <span key={c.field}><br /><span className="pill warn">Check</span> {c.field}: keeps “{c.kept}”, new value “{c.incoming}” saved for review</span>)}
            </p>
          ))}
        </details>
      )}
      {preview.duplicates.length > 0 && (
        <details className="section">
          <summary>Duplicates inside this file ({s.duplicates_in_file})</summary>
          {preview.duplicates.slice(0, 50).map((d) => <p key={d.row} style={{ margin: "4px 0" }}>Row {d.row} is the same as row {d.same_as_row} ({d.matched_by}); it only fills gaps.</p>)}
        </details>
      )}
      {leftOut.length > 0 && (
        <details className="section" open>
          <summary>Left out ({leftOut.length})</summary>
          {leftOut.slice(0, 30).map((r) => <p key={r.row} style={{ margin: "4px 0" }}>Row {r.row}: {r.reason}</p>)}
          <button className="btn ghost" onClick={() => downloadLeftOut(preview.columns, leftOut)}>Download left-out rows (CSV)</button>
        </details>
      )}
      {warnings.length > 0 && (
        <details className="section">
          <summary>Things we fixed or noticed ({warnings.reduce((n, [, c]) => n + c, 0)})</summary>
          <ul>{warnings.map(([k, n]) => <li key={k}>{WARNING_LABELS[k] || k}: {n}</li>)}</ul>
        </details>
      )}
      <details className="section">
        <summary>First rows after clean-up</summary>
        <table className="stack-table">
          <thead><tr><th>Row</th><th>Company</th><th>Person</th><th>Email / phone</th><th>Place</th><th>What happens</th></tr></thead>
          <tbody>
            {preview.rows.map((r) => (
              <tr key={r.row}>
                <td data-label="Row">{r.row}</td>
                <td data-label="Company">{r.company || r.domain || "—"}{r.company_size_bucket ? ` · ${r.company_size_bucket}` : ""}</td>
                <td data-label="Person">{r.contact_name || "—"}{r.title ? `, ${r.title}` : ""}</td>
                <td data-label="Contact" className="samples">{[r.email, r.phone].filter(Boolean).join(" · ") || "—"}</td>
                <td data-label="Place">{[r.city, r.state, r.country].filter(Boolean).join(", ") || "—"}</td>
                <td data-label="What happens">{ACTIONS[r.action] || r.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}

export default function ImportWizard({ onClose, onDone }) {
  const [step, setStep] = useState(0);
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState("");
  useSourceRegistry();
  const [source, setSource] = useState("");
  const [preview, setPreview] = useState(null);
  const [mapping, setMapping] = useState({});
  const [remember, setRemember] = useState(true);
  const [options, setOptions] = useState({ skip_role_addresses: false, skip_free_mail: false });
  const [result, setResult] = useState(null);
  const [undone, setUndone] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const body = (extra = {}) => ({ csv, source, mapping, options, remember_mapping: remember, ...extra });
  const run = async (work) => {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const check = (nextStep, extra) => run(async () => { setPreview(await api.importPreview(body(extra))); setStep(nextStep); });
  const changeOptions = (next) => { setOptions(next); run(async () => setPreview(await api.importPreview(body({ options: next })))); };
  const commit = () => run(async () => { setResult(await api.importCommit(body())); setStep(3); onDone?.(); });
  const undo = (id) => api.importUndo(id).then((r) => { setUndone(r); onDone?.(); return r; });
  const readFile = (file) => { if (!file) return; setFileName(file.name); file.text().then(setCsv); };
  if (!canWrite()) return null;                // viewers can't import (the server refuses)

  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>Import leads</h3>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      <ol className="steps wizard-steps" aria-label="Import steps">
        {STEPS.map((s, i) => <li key={s} className={i === step ? "on" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>{i + 1}. {s}</li>)}
      </ol>
      {error && <div className="tip error" role="alert" style={{ marginBottom: 10 }}>{error}</div>}

      {step === 0 && (
        <>
          <p className="sub" style={{ margin: "0 0 8px" }}>
            Any export works: Apollo, Sales Navigator, Lusha, ZoomInfo, Hunter, job boards, IndiaMART, Google Maps, the team sheet or your own columns.
            Duplicates are found and merged; nothing is saved until the last step.
          </p>
          <label htmlFor="import-file">Upload a CSV file (save Excel sheets as CSV first)</label>
          <input id="import-file" type="file" accept=".csv,.tsv,.txt,text/csv" onChange={(e) => readFile(e.target.files[0])} />
          {fileName && <small className="sub-inline">Loaded {fileName}</small>}
          <label htmlFor="import-paste">Or paste the rows (first row = column names; copying from Excel or Google Sheets works)</label>
          <textarea id="import-paste" rows={6} value={csv} onChange={(e) => { setCsv(e.target.value); setFileName(""); }} />
          <label htmlFor="import-source">Where did this list come from?</label>
          <select id="import-source" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">Work it out from the columns</option>
            {sourceOptions(["outbound", "import", "referral", "manual"]).map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" disabled={busy || !csv.trim()} onClick={() => { setMapping({}); check(1, { mapping: {} }); }}>
              {busy ? "Reading…" : "Next: check the columns"}
            </button>
          </div>
          {undone && <div className="tip" style={{ marginTop: 10 }}>Removed {undone.removed} lead(s); kept {undone.kept.length} that changed or were contacted since. {undone.note}</div>}
          <History onUndo={(id) => undo(id).then(() => true, (e) => { setError(e.message); return false; })} />
        </>
      )}

      {step === 1 && preview && (
        <>
          <MappingStep preview={preview} mapping={mapping} setMapping={setMapping} remember={remember} setRemember={setRemember} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn ghost" onClick={() => setStep(0)}>Back</button>
            <button className="btn" disabled={busy} onClick={() => check(2)}>{busy ? "Checking…" : "Next: dry run"}</button>
          </div>
        </>
      )}

      {step === 2 && preview && (
        <>
          <DryRun preview={preview} options={options} setOptions={changeOptions} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn ghost" onClick={() => setStep(1)}>Back to columns</button>
            <button className="btn" disabled={busy || preview.summary.create + preview.summary.merge + preview.summary.duplicates_in_file === 0} onClick={commit}>
              {busy ? "Importing…" : `Import: ${preview.summary.create} new, ${preview.summary.merge} updated`}
            </button>
          </div>
        </>
      )}

      {step === 3 && result && (
        <>
          <p>
            Done. Added <b>{result.summary.create}</b> new lead(s) and added details to <b>{result.summary.merge}</b> existing lead(s).
            {result.summary.conflicts > 0 && ` ${result.summary.conflicts} different value(s) are waiting for you under Data hygiene.`}
            {result.summary.rejected + result.summary.skipped > 0 && ` ${result.summary.rejected + result.summary.skipped} row(s) were left out.`}
          </p>
          {undone ? (
            <div className="tip">
              Undone: removed {undone.removed} lead(s).{undone.kept.length > 0 && ` Kept ${undone.kept.length}: ${undone.kept.map((k) => `${k.company || k.lead_id} (${k.reason})`).join("; ")}.`} {undone.note}
            </div>
          ) : (
            <button className="btn ghost danger" onClick={() => run(() => undo(result.import_id))}>Undo this import</button>
          )}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </>
      )}
    </div>
  );
}
