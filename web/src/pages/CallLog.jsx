import { useEffect, useState } from "react";
import { api, callLogQuery } from "../api.js";
import { Empty, ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import LeadQualityCard from "./LeadQualityCard.jsx";
import DownloadButton from "../DownloadButton.jsx";
import LeadDrawer from "./LeadDrawer.jsx";

const OUTCOMES = ["connected", "no_answer", "busy", "call_back", "interested", "not_interested", "meeting_booked", "wrong_number", "call_disconnected", "inactive_number"];
const CHANNELS = [["call", "Calls"], ["all", "All contact"], ["email", "Email"], ["whatsapp", "WhatsApp"], ["linkedin", "LinkedIn"]];
const TONE = { connected: "ok", interested: "ok", meeting_booked: "ok", call_back: "warm", no_answer: "", busy: "", not_interested: "bad", wrong_number: "bad", inactive_number: "bad", call_disconnected: "" };
const label = (o) => (o ? o.replace(/_/g, " ") : "");
const istToday = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
const istDaysAgo = (n) => new Date(Date.now() + 330 * 60000 - n * 86400000).toISOString().slice(0, 10);
const PAGE = 50;

export default function CallLog() {
  const today = istToday();
  const [f, setF] = useState({ from: today, to: today, by: "", outcome: "", q: "", channel: "call" });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);

  const params = { ...f, page, size: PAGE };
  const load = () => api.callLog(params).then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [f, page]);
  useEffect(() => { const t = setTimeout(() => { setPage(1); setF((x) => (x.q === q.trim() ? x : { ...x, q: q.trim() })); }, 300); return () => clearTimeout(t); }, [q]);

  const set = (patch) => { setPage(1); setF((x) => ({ ...x, ...patch })); };
  const preset = (from, to) => set({ from, to });

  if (error && !data) return <ErrorState message="Could not load the call log" detail={error} onRetry={load} />;
  const rows = data?.rows || [];
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const exportQuery = callLogQuery({ from: f.from, to: f.to, by: f.by, outcome: f.outcome, q: f.q, channel: f.channel });

  return (
    <>
      <h2>Call log</h2>
      <p className="sub">Every contact attempt across your leads, with who made it, when (IST), how it ended and the notes. You see only leads you can see.</p>

      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <button className={`tab-chip${f.from === today && f.to === today ? " on" : ""}`} onClick={() => preset(today, today)}>Today</button>
        <button className={`tab-chip${f.from === istDaysAgo(1) && f.to === istDaysAgo(1) ? " on" : ""}`} onClick={() => preset(istDaysAgo(1), istDaysAgo(1))}>Yesterday</button>
        <button className={`tab-chip${f.from === istDaysAgo(6) && f.to === today ? " on" : ""}`} onClick={() => preset(istDaysAgo(6), today)}>Last 7 days</button>
        <button className={`tab-chip${!f.from && !f.to ? " on" : ""}`} onClick={() => preset("", "")}>All time</button>
        <DownloadButton path={`/api/call-log/export?${exportQuery}`} filename="call-log.csv" label="Download CSV" testId="download-call-log"
          title="The rows matching these filters, as a CSV that opens in Excel" />
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <label>From <input type="date" aria-label="From date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} /></label>
        <label>To <input type="date" aria-label="To date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} /></label>
        <select aria-label="Caller" value={f.by} onChange={(e) => set({ by: e.target.value })}>
          <option value="">All callers</option>
          {(data?.callers || []).map((c) => <option key={c} value={c}>{c}</option>)}
          {f.by && !(data?.callers || []).includes(f.by) && <option value={f.by}>{f.by}</option>}
        </select>
        <select aria-label="Outcome" value={f.outcome} onChange={(e) => set({ outcome: e.target.value })}>
          <option value="">Any outcome</option>
          {OUTCOMES.map((o) => <option key={o} value={o}>{label(o)}</option>)}
        </select>
        <select aria-label="Channel" value={f.channel} onChange={(e) => set({ channel: e.target.value })}>
          {CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input aria-label="Search" placeholder="Search company, name, phone, notes" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
      </div>

      <LeadQualityCard />
      {data && data.summary.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }} data-testid="call-summary">
          <h3 style={{ marginTop: 0 }}>By caller</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Caller</th><th>Calls</th><th>Connected</th><th>Unanswered</th><th>Interested</th><th>Meetings</th><th>Connect rate</th></tr></thead>
              <tbody>
                {data.summary.map((s) => (
                  <tr key={s.caller} style={{ cursor: "pointer" }} onClick={() => set({ by: f.by === s.caller ? "" : s.caller })} title="Show only this caller">
                    <td><b>{s.caller}</b></td><td>{s.calls}</td><td>{s.connected}</td><td>{s.unanswered}</td><td>{s.interested}</td><td>{s.meetings}</td><td>{s.connect_rate}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!data && <p className="empty">Loading…</p>}
      {error && data && <p className="error" role="alert">{error}</p>}
      {data && rows.length === 0 && <Empty>No calls match these filters.</Empty>}
      {rows.length > 0 && (
        <div className="card">
          <div className="table-wrap">
            <table data-testid="call-log-table">
              <thead><tr><th>When (IST)</th><th>Caller</th><th>Lead</th><th>Phone</th><th>Channel</th><th>Outcome</th><th>Notes</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id || `${r.lead_id}-${r.at}-${i}`} style={{ cursor: "pointer" }} onClick={() => setOpen(r.lead_id)} tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") setOpen(r.lead_id); }}>
                    <td>{istLabel(r.at)}</td>
                    <td>{r.by || "Unknown"}</td>
                    <td><b>{r.company || "(no company)"}</b>{r.contact_name ? <div className="sub">{r.contact_name}</div> : null}</td>
                    <td>{r.phone}</td>
                    <td>{r.channel}</td>
                    <td>{r.outcome ? <span className={`pill ${TONE[r.outcome] || ""}`}>{label(r.outcome)}</span> : ""}</td>
                    <td style={{ maxWidth: 320, whiteSpace: "normal" }}>{r.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
            <span className="sub">{data.total} total · page {page} of {pages}</span>
            <span className="row" style={{ gap: 8 }}>
              <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </span>
          </div>
        </div>
      )}
      {open && <LeadDrawer id={open} ids={rows.map((r) => r.lead_id)} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
