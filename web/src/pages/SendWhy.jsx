import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";

const ORIGIN_PILL = { automatic: "", manual: "ok", unknown: "warn" };
const STATUS_LABEL = { dry_run: "practice", provider_error: "provider problem" };

// "Automatic" (the System sent it), "Manual" (a person did) or "Before tracking" (sent before 9 Nov 2026).
export function OriginPill({ type, label, actor }) {
  const title = type === "manual" ? `Sent by ${actor || "a person"}` : type === "automatic" ? "Sent automatically by the System"
    : "Sent before audit tracking (9 Nov 2026)";
  return <span className={`pill ${ORIGIN_PILL[type] ?? ""}`} data-testid="origin-pill" title={title}>{label || "Before tracking"}</span>;
}

// The chain behind one message, in plain English (one line per fact).
export function WhyList({ lines }) {
  if (!lines?.length) return null;
  return (
    <ul data-testid="why-list" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
      {lines.map((line, i) => <li key={i}><small>{line}</small></li>)}
    </ul>
  );
}

// Owner / admin tab: alerts, reports, password resets, reminders and invites, with why each went out.
export function InternalEmails() {
  const [kind, setKind] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  useEffect(() => { setError(""); ops.outboxInternal({ kind }).then(setData).catch((e) => setError(e.message)); }, [kind]);
  return (
    <>
      <div className="card" style={{ marginBottom: 10, padding: 12 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <select aria-label="Internal email kind" value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: "auto" }}>
            <option value="">Every kind</option>{(data?.kinds || []).map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
          </select>
          <small className="sub-inline">Emails the app sends to the team and for sign-in: they never count against the cold-email limit.</small>
        </div>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {!data ? !error && <p className="empty">Loading…</p> : data.items.length === 0 ? <p className="empty">No internal emails yet.</p> : (
          <table data-testid="internal-table">
            <thead><tr><th>When (IST)</th><th>To</th><th>Kind</th><th>Subject</th><th>Why</th><th>Status</th></tr></thead>
            <tbody>{data.items.map((m) => (
              <tr key={m.id || `${m.at}-${m.to}`}>
                <td data-label="When">{istLabel(m.at)}</td>
                <td data-label="To">{m.to}</td>
                <td data-label="Kind">{m.kind_label}</td>
                <td data-label="Subject">{m.subject}</td>
                <td data-label="Why"><OriginPill type={m.origin_type} label={m.origin_label} actor={m.origin_actor} />{" "}
                  <button className="btn ghost" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}>Why it was sent</button>
                  {open === m.id && <WhyList lines={m.why} />}</td>
                <td data-label="Status"><span className={`pill ${m.status === "sent" ? "ok" : m.status === "failed" ? "warn" : ""}`} title={m.error || ""}>
                  {STATUS_LABEL[m.status] || m.status}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {data && data.total > data.items.length && <small className="sub-inline">Showing the newest {data.items.length} of {data.total}.</small>}
    </>
  );
}
