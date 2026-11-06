import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";
import DownloadButton from "../DownloadButton.jsx";

// Owner only: who did what and when (stage moves, Send for real, bulk actions, erasures, logins, workspaces, settings).
const EMPTY = { action: "", by: "", q: "", from: "", to: "" };
const PAGE = 50;
const GROUPS = [["", "Every action"], ["stage", "Stage changes"], ["live_sending", "Send for real"], ["bulk", "Bulk actions"],
  ["lead.erase", "Erasures"], ["user", "Logins and roles"], ["workspace", "Workspaces"], ["linkedin_agent", "LinkedIn agent"],
  ["settings", "Settings saved"], ["contact.edit", "Contact corrections"], ["list.delete", "Lists deleted"], ["campaign.delete", "Campaigns deleted"],
  ["export", "Downloads (CSV/JSON)"], ["backup", "Backups and restores"]];

export default function AuditLog() {
  const [f, setF] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    ops.audit({ ...f, limit: PAGE, offset }).then(setData).catch((e) => setError(e.message));
  }, [JSON.stringify(f), offset]);
  const set = (k, v) => { setF({ ...f, [k]: v }); setOffset(0); };
  const label = (a) => data?.actions?.[a] || a;
  return (
    <div data-testid="audit-log">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div><h3 style={{ margin: 0 }}>Audit log</h3>
          <p className="sub" style={{ margin: "4px 0 0" }}>Who did what, and when (IST). Settings saves list the names of what changed, never secret values.</p></div>
        <DownloadButton path={ops.auditCsvPath(f)} label="Download CSV" filename="audit-log.csv" testId="download-audit" />
      </div>
      <div className="card" style={{ margin: "10px 0", padding: 12 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <select aria-label="Action" value={f.action} onChange={(e) => set("action", e.target.value)} style={{ width: "auto" }}>
            {GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Person" value={f.by} onChange={(e) => set("by", e.target.value)} style={{ width: "auto" }}>
            <option value="">Anyone</option>{(data?.people || []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input aria-label="Search what or detail" placeholder="Search what changed" value={f.q} onChange={(e) => set("q", e.target.value)} style={{ flex: "1 1 160px" }} />
          <label className="row" style={{ gap: 4, margin: 0 }}>From <input type="date" aria-label="From (IST day)" value={f.from} onChange={(e) => set("from", e.target.value)} style={{ width: "auto" }} /></label>
          <label className="row" style={{ gap: 4, margin: 0 }}>To <input type="date" aria-label="To (IST day)" value={f.to} onChange={(e) => set("to", e.target.value)} style={{ width: "auto" }} /></label>
          {Object.values(f).some(Boolean) && <button className="btn ghost" onClick={() => { setF(EMPTY); setOffset(0); }}>Clear</button>}
        </div>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="card table-card" style={{ padding: 0 }}>
        {!data ? <p className="empty">Loading…</p> : data.items.length === 0 ? <p className="empty">Nothing recorded for these filters.</p> : (
          <div className="table-scroll"><table data-testid="audit-table">
            <thead><tr><th>When (IST)</th><th>Who</th><th>Action</th><th>What</th><th>Detail</th></tr></thead>
            <tbody>{data.items.map((r, i) => (
              <tr key={`${r.at}-${i}`}>
                <td data-label="When"><small>{istLabel(r.at)}</small></td>
                <td data-label="Who">{r.by}{r.by_role && <><br /><small className="sub-inline">{r.by_role}</small></>}</td>
                <td data-label="Action"><span className="pill">{label(r.action)}</span></td>
                <td data-label="What">{r.target}</td>
                <td data-label="Detail"><small>{r.detail}</small></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      {data && data.total > PAGE && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <small className="sub-inline">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</small>
          <span className="row" style={{ gap: 6 }}>
            <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
            <button className="btn ghost" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Older</button>
          </span>
        </div>
      )}
    </div>
  );
}
