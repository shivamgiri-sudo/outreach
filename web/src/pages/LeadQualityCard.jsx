import { useEffect, useState } from "react";
import { api } from "../api.js";
import { sourceLabel } from "../labels.js";

// Invalid / consultant rates per source and per caller, and why leads were rejected: tells you which data source is bad.
export default function LeadQualityCard() {
  const [d, setD] = useState(null);
  useEffect(() => { api.leadQuality().then(setD).catch(() => setD(null)); }, []);
  if (!d || !d.total.leads) return null;
  const t = d.total;
  return (
    <details className="card" style={{ marginBottom: 12 }} data-testid="lead-quality">
      <summary><b>Lead quality</b> · {t.invalid} invalid ({t.invalid_rate}%) · {t.consultant} consultant ({t.consultant_rate}%) of {t.leads} leads</summary>
      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table>
          <thead><tr><th>Source</th><th>Leads</th><th>Invalid</th><th>Invalid rate</th><th>Consultant</th><th>Consultant rate</th></tr></thead>
          <tbody>{d.sources.map((s) => <tr key={s.source}><td>{sourceLabel(s.source)}</td><td>{s.leads}</td><td>{s.invalid}</td>
            <td><span className={`pill ${s.invalid_rate >= 20 ? "err" : s.invalid_rate >= 10 ? "warn" : ""}`}>{s.invalid_rate}%</span></td><td>{s.consultant}</td><td>{s.consultant_rate}%</td></tr>)}</tbody>
        </table>
      </div>
      {d.reasons.length > 0 && <p className="sub" style={{ marginBottom: 4 }}>Why leads were rejected: {d.reasons.map((r) => `${d.reason_names[r.reason] || r.reason} ${r.count}`).join(" · ")}</p>}
      {d.callers.length > 0 && <p className="sub" style={{ margin: 0 }}>Marked by: {d.callers.map((c) => `${c.caller} (${c.invalid} invalid, ${c.consultant} consultant)`).join(" · ")}</p>}
    </details>
  );
}
