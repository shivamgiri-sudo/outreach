import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";

const STATUS_PILL = { sent: "ok", delivered: "ok", dry_run: "", failed: "warn", provider_error: "warn" };
const STATUS_LABEL = { dry_run: "practice", provider_error: "provider problem" };
const EMPTY = { status: "", step: "", from: "", to: "", campaign_id: "", kind: "", q: "" };
const PAGE_SIZE = 25;

function EmailViewer({ itemKey, onClose, onOpenLead }) {
  const [m, setM] = useState(null);
  const [view, setView] = useState("text");
  const [error, setError] = useState("");
  useEffect(() => { ops.outboxMessage(itemKey).then(setM).catch((e) => setError(e.message)); }, [itemKey]);
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Email">
        <div className="drawer-head">
          <div><h2 style={{ margin: 0 }}>{m?.subject || "Email"}</h2>
            {m && <small className="sub-inline">To {m.to} · {m.step} · {istLabel(m.sent_at)}{m.by ? ` · by ${m.by}` : ""}</small>}</div>
          <button className="btn ghost" onClick={onClose} aria-label="Close">Close</button>
        </div>
        {error && <div className="tip error" role="alert">{error}</div>}
        {!m ? !error && <p className="empty">Loading…</p> : (
          <>
            <div className="row" style={{ marginBottom: 10, flexWrap: "wrap" }}>
              <span className={`pill ${STATUS_PILL[m.delivered_at ? "delivered" : m.status] ?? "warn"}`}>{m.delivered_at ? "delivered" : STATUS_LABEL[m.status] || m.status}</span>
              {m.campaign && <span className="pill">{m.campaign}</span>}
              {m.lead_id && <button className="btn ghost" onClick={() => onOpenLead(m.lead_id)}>Open lead{m.company ? `: ${m.company}` : ""}</button>}
            </div>
            {m.error && <div className="tip error">{m.error}</div>}
            <div className="tabs" role="tablist" aria-label="Email view">
              <button role="tab" aria-selected={view === "text"} className={view === "text" ? "on" : ""} onClick={() => setView("text")}>Text as sent</button>
              <button role="tab" aria-selected={view === "html"} className={view === "html" ? "on" : ""} disabled={!m.html} onClick={() => setView("html")}>Designed preview</button>
            </div>
            {view === "text"
              ? <pre data-testid="email-body" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", font: "inherit", margin: 0 }}>{m.body || "(no text kept)"}</pre>
              : <iframe title="Designed email preview" srcDoc={m.html} sandbox="" style={{ width: "100%", height: "65vh", border: "1px solid var(--line)", borderRadius: 10, background: "#fff" }} />}
          </>
        )}
      </aside>
    </>
  );
}

// The emails table: filtered and paged on the server; a row opens the lead, "View" shows the whole email.
export default function OutboxTable({ refreshKey }) {
  const [filters, setFilters] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const [viewing, setViewing] = useState(null);
  const load = () => { setError(""); ops.outboxPage({ ...filters, page, size: PAGE_SIZE }).then(setData).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, [JSON.stringify(filters), page, refreshKey]);
  const set = (k, v) => { setFilters({ ...filters, [k]: v }); setPage(1); };
  const any = Object.values(filters).some(Boolean);
  return (
    <>
      <div className="card" style={{ marginBottom: 10, padding: 12 }} data-testid="outbox-filters">
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <input aria-label="Search to, subject or company" placeholder="Search to, subject, company" value={filters.q} onChange={(e) => set("q", e.target.value)} style={{ flex: "1 1 180px" }} />
          <select aria-label="Status" value={filters.status} onChange={(e) => set("status", e.target.value)} style={{ width: "auto" }}>
            <option value="">Any status</option><option value="sent">Sent</option><option value="delivered">Delivered</option>
            <option value="practice">Practice</option><option value="failed">Failed</option>
          </select>
          <select aria-label="Step" value={filters.step} onChange={(e) => set("step", e.target.value)} style={{ width: "auto" }}>
            <option value="">Any step</option>{(data?.steps || []).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select aria-label="Campaign" value={filters.campaign_id} onChange={(e) => set("campaign_id", e.target.value)} style={{ width: "auto" }}>
            <option value="">Any campaign</option>{(data?.campaigns || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select aria-label="Kind" value={filters.kind} onChange={(e) => set("kind", e.target.value)} style={{ width: "auto" }}>
            <option value="">Sequence and one-off</option><option value="sequence">Sequence only</option><option value="manual">One-off (manual) only</option>
          </select>
          <label className="row" style={{ gap: 4, margin: 0 }}>From <input type="date" aria-label="Sent from (IST day)" value={filters.from} onChange={(e) => set("from", e.target.value)} style={{ width: "auto" }} /></label>
          <label className="row" style={{ gap: 4, margin: 0 }}>To <input type="date" aria-label="Sent to (IST day)" value={filters.to} onChange={(e) => set("to", e.target.value)} style={{ width: "auto" }} /></label>
          {any && <button className="btn ghost" onClick={() => { setFilters(EMPTY); setPage(1); }}>Clear filters</button>}
        </div>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {!data ? <p className="empty">Loading…</p> : data.items.length === 0 ? (
          <p className="empty">{any ? "No emails match these filters." : "Nothing queued yet. Start a sequence from a lead, then click Run."}</p>
        ) : (
          <table data-testid="outbox-table">
            <thead><tr><th>When (IST)</th><th>To</th><th>Step</th><th>Subject</th><th>Status</th><th /></tr></thead>
            <tbody>{data.items.map((m) => (
              <tr key={m.key} className="click" onClick={() => m.lead_id && setLead(m.lead_id)} title={m.lead_id ? "Open the lead" : ""}>
                <td data-label="When">{istLabel(m.sent_at)}</td>
                <td data-label="To">{m.to}{m.company && <><br /><small>{m.company}</small></>}</td>
                <td data-label="Step">{m.step}{m.manual ? <><br /><small>one-off{m.by ? ` by ${m.by}` : ""}</small></> : m.by && <><br /><small>sent now by {m.by}</small></>}{m.campaign && <><br /><small>{m.campaign}</small></>}</td>
                <td data-label="Subject">{m.subject}{m.ai_drafted && <> <span className="pill" data-testid="ai-drafted-pill" title={`Written by the AI; approved and sent by ${m.ai_approved_by || "a person"}`}>AI-drafted</span></>}<br /><small className="sub-inline">{m.preview}</small></td>
                <td data-label="Status"><span className={`pill ${STATUS_PILL[m.delivered_at ? "delivered" : m.status] ?? "warn"}`} title={m.error || ""}>
                  {m.delivered_at ? "delivered" : STATUS_LABEL[m.status] || m.status}</span>
                  {m.opens > 0 && <><br /><span className="pill" data-testid="opened-pill" title={`First opened ${istLabel(m.opened_at)}. Mail apps can open emails by themselves, so treat opens as a rough sign.`}>opened{m.opens > 1 ? ` ×${m.opens}` : ""}</span></>}
                  {m.clicks > 0 && <> <span className="pill ok" data-testid="clicked-pill" title={`First click ${istLabel(m.clicked_at)}`}>clicked{m.clicks > 1 ? ` ×${m.clicks}` : ""}</span></>}</td>
                <td><button className="btn ghost" onClick={(e) => { e.stopPropagation(); setViewing(m.key); }} aria-label={`View email to ${m.to}`}>View</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {data && data.total > 0 && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8, flexWrap: "wrap" }}>
          <small className="sub-inline">{data.total} email{data.total === 1 ? "" : "s"}{data.total !== data.all ? ` of ${data.all}` : ""} · page {data.page} of {data.pages}</small>
          <span className="row" style={{ gap: 6 }}>
            <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="btn ghost" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Next</button>
          </span>
        </div>
      )}
      {viewing && <EmailViewer itemKey={viewing} onClose={() => setViewing(null)} onOpenLead={(id) => { setViewing(null); setLead(id); }} />}
      {lead && <LeadDrawer id={lead} onClose={() => setLead(null)} />}
    </>
  );
}
