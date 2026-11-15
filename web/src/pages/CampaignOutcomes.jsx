import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { stageLabel } from "../labels.js";
import "../campaigns.css";
import { Badge, Button, Card, DataTable, EmptyState, Input, Select, Skeleton } from "../ui/index.js";

// What a campaign's leads did: sent, opened, replied, interested, meetings (rates are of the leads emailed).
export function OutcomeLine({ o }) {
  if (!o) return null;
  return (
    <dl className="camp-mini" data-testid="campaign-outcomes" aria-label="Outcomes">
      {[["Leads", o.leads], ["Sent", o.emails_sent], ["Opened", `${o.opened} (${o.rates.open}%)`], ["Replied", `${o.replied} (${o.rates.reply}%)`]].map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

export function OutcomeTiles({ o }) {
  if (!o) return null;
  const tiles = [["Leads", o.leads], ["Emails sent", o.emails_sent, `${o.leads_emailed} leads emailed`], ["Opened", o.opened, `${o.rates.open}%`],
    ["Replied", o.replied, `${o.rates.reply}%`], ["Interested", o.interested, `${o.rates.interested}%`], ["Meetings", o.meetings, `${o.rates.meeting}%`],
    ["Customers", o.customers], ["Unsubscribed", o.unsubscribed]];
  return (
    <div className="camp-stats" data-testid="campaign-outcome-tiles">
      {tiles.map(([label, value, note]) => (
        <div className="card stat" key={label}>
          <b>{value ?? 0}</b><span>{label}</span>{note && <small className="sub-inline">{note}</small>}
        </div>
      ))}
    </div>
  );
}

const STATES = [["", "Any outreach state"], ["in_sequence", "In the sequence"], ["not_started", "Not started"], ["ready", "Ready for email"], ["replied", "Replied"]];
const PAGE_SIZE = 25;
const BAND = { hot: "hot", warm: "warning", cold: "muted" };

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
      <div className="camp-filters" data-testid="campaign-lead-filters">
        <Input icon="search" aria-label="Search campaign leads" placeholder="Search company, contact, email" value={f.q} onChange={(e) => set("q", e.target.value)} />
        <Select aria-label="Stage" value={f.stage} onChange={(e) => set("stage", e.target.value)}>
          <option value="">Any stage</option>{(data?.stages || []).map((s) => <option key={s} value={s}>{stageLabel(s)}</option>)}
        </Select>
        <Select aria-label="Outreach state" value={f.state} onChange={(e) => set("state", e.target.value)}>
          {STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <Select aria-label="Sort" value={f.sort} onChange={(e) => set("sort", e.target.value)}>
          <option value="score">Highest score first</option><option value="company">Company A–Z</option><option value="recent">Newest first</option>
        </Select>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <Card className="table-card">
        {!data ? <div className="camp-skel u-p-16" aria-busy="true"><Skeleton height={20} /><Skeleton height={20} /><Skeleton height={20} /></div> : data.items.length === 0 ? (
          data.all ? <EmptyState icon="filter" title="No leads match these filters">Clear the search or pick “Any stage” to see them all.</EmptyState>
            : <EmptyState icon="rocket" title="No leads yet">Press “Run now (preview)” to see what the campaign would find, or “Run for real” to add the leads.</EmptyState>
        ) : (
          <DataTable label="Campaign leads" tableClass="leads-table" data-testid="campaign-leads">
            <thead><tr><th>Company</th><th>Contact</th><th>Stage</th><th>Score</th><th>Ready for email?</th></tr></thead>
            <tbody>
              {data.items.map((l) => (
                <tr key={l.id} className="click" onClick={() => onOpen(l.id)}>
                  <td data-label="Company"><button className="linkish" onClick={(e) => { e.stopPropagation(); onOpen(l.id); }}><b>{l.company || l.domain}</b></button></td>
                  <td data-label="Contact">{l.contact_name || "—"}<br /><small>{l.email || l.phone || ""}</small></td>
                  <td data-label="Stage">{stageLabel(l.stage)}{l.sequence ? " · in sequence" : ""}</td>
                  <td data-label="Score"><Badge tone={BAND[l.band] || "neutral"}>{l.score} · {l.band}</Badge></td>
                  <td data-label="Ready for email?">{l.sequence ? "Started" : l.campaign_check?.qualified ? <Badge tone="success">Yes</Badge>
                    : <small>{(l.campaign_check?.reasons || ["not checked yet"]).join("; ")}</small>}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </Card>
      {data && data.total > PAGE_SIZE && (
        <div className="camp-pager">
          <small className="sub-inline">{data.total} leads · page {data.page} of {data.pages}</small>
          <span className="row u-gap-8">
            <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button variant="secondary" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Next</Button>
          </span>
        </div>
      )}
    </>
  );
}
