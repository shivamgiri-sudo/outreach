import { useEffect, useState } from "react";
import { api, callLogQuery } from "../api.js";
import { Empty, ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import LeadQualityCard from "./LeadQualityCard.jsx";
import DownloadButton from "../DownloadButton.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import { Button, Cell, DataTable, Field, Input, PageHeader, Segmented, Select } from "../ui/index.js";

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
  const PRESETS = { today: () => set({ from: today, to: today }), yesterday: () => set({ from: istDaysAgo(1), to: istDaysAgo(1) }),
    week: () => set({ from: istDaysAgo(6), to: today }), all: () => set({ from: "", to: "" }) };
  const range = f.from === today && f.to === today ? "today" : f.from === istDaysAgo(1) && f.to === istDaysAgo(1) ? "yesterday"
    : f.from === istDaysAgo(6) && f.to === today ? "week" : !f.from && !f.to ? "all" : "";

  if (error && !data) return <ErrorState message="Could not load the call log" detail={error} onRetry={load} />;
  const rows = data?.rows || [];
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const exportQuery = callLogQuery({ from: f.from, to: f.to, by: f.by, outcome: f.outcome, q: f.q, channel: f.channel });

  return (
    <>
      <PageHeader title="Call log"
        description={<>Every contact attempt across your leads, with who made it, when (IST), how it ended and the notes. You see only leads you can see.</>} />

      <div className="page-toolbar">
        <Segmented label="Date range" value={range} onChange={(id) => PRESETS[id]()}
          items={[{ id: "today", label: "Today" }, { id: "yesterday", label: "Yesterday" }, { id: "week", label: "Last 7 days" }, { id: "all", label: "All time" }]} />
        <span className="count">
          <DownloadButton path={`/api/call-log/export?${exportQuery}`} filename="call-log.csv" label="Download CSV" testId="download-call-log"
            title="The rows matching these filters, as a CSV that opens in Excel" />
        </span>
      </div>

      <div className="page-toolbar">
        <Field label="From"><Input type="date" aria-label="From date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} /></Field>
        <Field label="To"><Input type="date" aria-label="To date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} /></Field>
        <Select aria-label="Caller" value={f.by} onChange={(e) => set({ by: e.target.value })}>
          <option value="">All callers</option>
          {(data?.callers || []).map((c) => <option key={c} value={c}>{c}</option>)}
          {f.by && !(data?.callers || []).includes(f.by) && <option value={f.by}>{f.by}</option>}
        </Select>
        <Select aria-label="Outcome" value={f.outcome} onChange={(e) => set({ outcome: e.target.value })}>
          <option value="">Any outcome</option>
          {OUTCOMES.map((o) => <option key={o} value={o}>{label(o)}</option>)}
        </Select>
        <Select aria-label="Channel" value={f.channel} onChange={(e) => set({ channel: e.target.value })}>
          {CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <Input icon="search" aria-label="Search" placeholder="Search company, name, phone, notes" value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} />
      </div>

      <LeadQualityCard />
      {data && data.summary.length > 0 && (
        <div className="card table-card u-mb-12" data-testid="call-summary">
          <h3 className="table-title">By caller</h3>
          <div>
            <DataTable label="Calls by caller">
              <thead><tr><th>Caller</th><th>Calls</th><th>Connected</th><th>Unanswered</th><th>Interested</th><th>Meetings</th><th>Connect rate</th></tr></thead>
              <tbody>
                {data.summary.map((s) => (
                  <tr key={s.caller} className="u-pointer" onClick={() => set({ by: f.by === s.caller ? "" : s.caller })} title="Show only this caller">
                    <td><b>{s.caller}</b></td><td>{s.calls}</td><td>{s.connected}</td><td>{s.unanswered}</td><td>{s.interested}</td><td>{s.meetings}</td><td>{s.connect_rate}%</td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </div>
        </div>
      )}

      {!data && <p className="empty">Loading…</p>}
      {error && data && <p className="error" role="alert">{error}</p>}
      {data && rows.length === 0 && <Empty>No calls match these filters.</Empty>}
      {rows.length > 0 && (
        <div className="card table-card">
          <div>
            <DataTable tableClass="call-log-table" label="Call log" data-testid="call-log-table">
              <thead><tr><th className="c-when">When (IST)</th><th className="c-who">Caller</th><th className="c-lead">Lead</th><th className="c-phone">Phone</th><th className="c-chan">Channel</th><th className="c-out">Outcome</th><th>Notes</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id || `${r.lead_id}-${r.at}-${i}`} className="u-pointer" onClick={() => setOpen(r.lead_id)} tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") setOpen(r.lead_id); }}>
                    <td><small className="clip" title={istLabel(r.at)}>{istLabel(r.at)}</small></td>
                    <td>{r.by || "Unknown"}</td>
                    <td><Cell title={r.company || "(no company)"} sub={r.contact_name || ""} /></td>
                    <td>{r.phone}</td>
                    <td>{r.channel}</td>
                    <td>{r.outcome ? <span className={`pill ${TONE[r.outcome] || ""}`}>{label(r.outcome)}</span> : ""}</td>
                    <td className="c-notes" title={r.notes}>{r.notes}</td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </div>
          <div className="pager" role="navigation" aria-label="Pages of calls">
            <span className="sub">{data.total} total · page {page} of {pages}</span>
            <span className="row">
              <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </span>
          </div>
        </div>
      )}
      {open && <LeadDrawer id={open} ids={rows.map((r) => r.lead_id)} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
