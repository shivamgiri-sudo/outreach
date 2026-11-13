import { useEffect, useState } from "react";
import { api, canAdminister } from "../api.js";
import { sourceLabel } from "../labels.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";

// Invalid / consultant rates per source and per caller, why leads were rejected, and (managers) the week's marked leads to
// review and restore. Tells you which data source is bad and which caller marks far more than the team.
export default function LeadQualityCard() {
  const [d, setD] = useState(null);
  const [review, setReview] = useState(null);
  const [msg, setMsg] = useState("");
  const confirm = useConfirm();
  const load = () => {
    api.leadQuality().then(setD).catch(() => setD(null));
    if (canAdminister()) api.leadQualityReview(7).then(setReview).catch(() => setReview(null));
  };
  useEffect(load, []);
  if (!d || !d.total.leads) return null;
  const t = d.total;
  const restore = async (r) => {
    if (!(await confirm({ message: `Restore ${r.company}? The ${r.kind} mark is removed${r.kind === "invalid" ? " and the lead goes back to its earlier stage" : ""}.`, confirmLabel: "Restore" }))) return;
    try { await api.restoreLeadQuality(r.lead_id); setMsg(`${r.company} restored.`); load(); } catch (e) { setMsg(e.message); }
  };
  const outliers = d.callers.filter((c) => c.outlier);
  return (
    <details className="card" style={{ marginBottom: 12 }} data-testid="lead-quality">
      <summary><b>Lead quality</b> · {t.invalid} invalid ({t.invalid_rate}%) · {t.consultant} consultant ({t.consultant_rate}%) of {t.leads} leads
        {outliers.length > 0 && <span className="pill warn" style={{ marginLeft: 8 }} data-testid="quality-outlier">Check {outliers.map((c) => c.caller).join(", ")}</span>}</summary>
      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table>
          <thead><tr><th>Source</th><th>Leads</th><th>Invalid</th><th>Invalid rate</th><th>Consultant</th><th>Consultant rate</th></tr></thead>
          <tbody>{d.sources.map((s) => <tr key={s.source}><td>{sourceLabel(s.source)}</td><td>{s.leads}</td><td>{s.invalid}</td>
            <td><span className={`pill ${s.invalid_rate >= 20 ? "err" : s.invalid_rate >= 10 ? "warn" : ""}`}>{s.invalid_rate}%</span></td><td>{s.consultant}</td><td>{s.consultant_rate}%</td></tr>)}</tbody>
        </table>
      </div>
      {d.reasons.length > 0 && <p className="sub" style={{ marginBottom: 4 }}>Why leads were rejected: {d.reasons.map((r) => `${d.reason_names[r.reason] || r.reason} ${r.count}`).join(" · ")}</p>}
      {d.callers.length > 0 && (
        <div className="table-wrap">
          <table data-testid="quality-callers">
            <thead><tr><th>Marked by</th><th>Invalid</th><th>Consultant</th><th>Calls made</th><th>Marked per call</th></tr></thead>
            <tbody>{d.callers.map((c) => <tr key={c.caller}><td>{c.caller}{c.outlier && <span className="pill warn" style={{ marginLeft: 6 }} title="Marks far more leads than the team does: worth a conversation">High</span>}</td>
              <td>{c.invalid}</td><td>{c.consultant}</td><td>{c.calls}</td><td>{c.calls ? `${c.rate}%` : "—"}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      {review && (
        <div data-testid="quality-review" style={{ marginTop: 10 }}>
          <b>Marked in the last {review.days} days ({review.rows.length})</b>
          {msg && <p className="sub" role="status" style={{ margin: "4px 0" }}>{msg}</p>}
          {review.rows.length === 0 ? <p className="sub">Nothing to review.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Lead</th><th>Marked</th><th>Why</th><th>By</th><th>When (IST)</th><th /></tr></thead>
                <tbody>{review.rows.map((r) => <tr key={r.lead_id}>
                  <td><b>{r.company}</b><div className="sub">{r.contact_name}</div></td><td><span className="pill warn">{r.kind === "invalid" ? "Invalid" : "Consultant"}</span></td>
                  <td>{r.kind === "invalid" ? (review.reason_names[r.reason] || r.reason || "—") : "—"}{r.note ? <div className="sub">{r.note}</div> : null}</td>
                  <td>{r.by}</td><td>{istLabel(r.at)}</td>
                  <td><button className="btn ghost" onClick={() => restore(r)} data-testid="quality-restore">Restore</button></td></tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </details>
  );
}
