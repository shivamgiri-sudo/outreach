import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { stageLabel } from "../labels.js";

// What a campaign's leads did: sent, opened, replied, interested, meetings (rates are of the leads emailed).
export function OutcomeLine({ o }) {
  if (!o) return null;
  return (
    <small className="sub-inline" data-testid="campaign-outcomes">
      {o.leads} leads · {o.emails_sent} sent · {o.opened} opened ({o.rates.open}%) · {o.replied} replied ({o.rates.reply}%) ·{" "}
      {o.interested} interested · {o.meetings} meetings
    </small>
  );
}

export function OutcomeTiles({ o }) {
  if (!o) return null;
  const tiles = [["Leads", o.leads], ["Emails sent", o.emails_sent, `${o.leads_emailed} leads emailed`], ["Opened", o.opened, `${o.rates.open}%`],
    ["Replied", o.replied, `${o.rates.reply}%`], ["Interested", o.interested, `${o.rates.interested}%`], ["Meetings", o.meetings, `${o.rates.meeting}%`],
    ["Customers", o.customers], ["Unsubscribed", o.unsubscribed]];
  return (
    <div className="grid g4" style={{ margin: "12px 0", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 130px), 1fr))" }} data-testid="campaign-outcome-tiles">
      {tiles.map(([label, value, note]) => (
        <div className="card stat" style={{ padding: 12 }} key={label}>
          <span className="sub-inline">{label}</span><b style={{ fontSize: 24, marginTop: 2 }}>{value ?? 0}</b>{note && <small className="sub-inline">{note}</small>}
        </div>
      ))}
    </div>
  );
}

const STATES = [["", "Any outreach state"], ["in_sequence", "In the sequence"], ["not_started", "Not started"], ["ready", "Ready for email"], ["replied", "Replied"]];
const PAGE_SIZE = 25;

// One campaign's leads, a page at a time, with filters (all on the server).
export function CampaignLeadsTable({ id, onOpen, onTotal, refreshKey }) {
  const [f, setF] = useState({ q: "", stage: "", state: "", sort: "score" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    ops.campaignLeadsPage(id, { ...f, page, size: PAGE_SIZE }).then((d) => { setData(d); onTotal?.(d.all); }).catch((e) => setError(e.message));
  }, [id, JSON.stringify(f), page, refreshKey]);
  const set = (k, v) => { setF({ ...f, [k]: v }); setPage(1); };
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 8 }} data-testid="campaign-lead-filters">
        <input aria-label="Search campaign leads" placeholder="Search company, contact, email" value={f.q} onChange={(e) => set("q", e.target.value)} style={{ flex: "1 1 180px" }} />
        <select aria-label="Stage" value={f.stage} onChange={(e) => set("stage", e.target.value)} style={{ width: "auto" }}>
          <option value="">Any stage</option>{(data?.stages || []).map((s) => <option key={s} value={s}>{stageLabel(s)}</option>)}
        </select>
        <select aria-label="Outreach state" value={f.state} onChange={(e) => set("state", e.target.value)} style={{ width: "auto" }}>
          {STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select aria-label="Sort" value={f.sort} onChange={(e) => set("sort", e.target.value)} style={{ width: "auto" }}>
          <option value="score">Highest score first</option><option value="company">Company A–Z</option><option value="recent">Newest first</option>
        </select>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="card table-card">
        {!data ? <p className="empty">Loading…</p> : data.items.length === 0 ? (
          <p className="empty">{data.all ? "No leads match these filters." : "No leads yet. Run the campaign to find some."}</p>
        ) : (
          <table className="leads-table" data-testid="campaign-leads">
            <thead><tr><th>Company</th><th>Contact</th><th>Stage</th><th>Score</th><th>Ready for email?</th></tr></thead>
            <tbody>
              {data.items.map((l) => (
                <tr key={l.id} className="click" onClick={() => onOpen(l.id)}>
                  <td data-label="Company"><button className="linkish" onClick={(e) => { e.stopPropagation(); onOpen(l.id); }}><b>{l.company || l.domain}</b></button></td>
                  <td data-label="Contact">{l.contact_name || "—"}<br /><small>{l.email || l.phone || ""}</small></td>
                  <td data-label="Stage">{stageLabel(l.stage)}{l.sequence ? " · in sequence" : ""}</td>
                  <td data-label="Score"><span className={`pill ${l.band}`}>{l.score} · {l.band}</span></td>
                  <td data-label="Ready for email?">{l.sequence ? "Started" : l.campaign_check?.qualified ? <span className="pill ok">Yes</span>
                    : <small>{(l.campaign_check?.reasons || ["not checked yet"]).join("; ")}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && data.total > PAGE_SIZE && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <small className="sub-inline">{data.total} leads · page {data.page} of {data.pages}</small>
          <span className="row" style={{ gap: 6 }}>
            <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="btn ghost" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Next</button>
          </span>
        </div>
      )}
    </>
  );
}
