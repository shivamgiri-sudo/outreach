import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import { sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import "../analytics-pages.css";
import { Button, Card, CardHeader, DataTable, EmptyState, Field, Input, PageHeader, Skeleton, Stat, Stats } from "../ui/index.js";

const STAGE_COLUMNS = ["new", "contacted", "engaged", "interested", "call_done", "customer", "unsubscribed", "disqualified"];
const money = (n) => (n == null ? "—" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);

// The owner types what each source costs per month; the page then shows what a lead and a customer cost.
function SourceSpend({ rows, saved, onSaved }) {
  const [values, setValues] = useState(() => Object.fromEntries(rows.map((r) => [r.source, saved[r.source] ?? ""])));
  const [state, setState] = useState("");
  const save = async () => {
    setState("Saving…");
    try {
      await api.saveSourceSpend(values);
      setState("Saved");
      onSaved();
    } catch (e) { setState(e.message); }
  };
  return (
    <Card as="details" className="an-details an-section">
      <summary><b>Cost per lead and per customer</b> <small className="sub-inline">type what you spend on each source per month</small></summary>
      <div className="form-grid u-mt-12">
        {rows.map((r) => (
          <Field key={r.source} label={`${sourceLabel(r.source)}: monthly spend (₹)`}>
            <Input id={`spend-${r.source}`} inputMode="decimal" value={values[r.source]} onChange={(e) => setValues({ ...values, [r.source]: e.target.value })} />
          </Field>
        ))}
      </div>
      <div className="row u-mt-12"><Button onClick={save}>Save spend</Button> <span role="status">{state}</span></div>
      <p className="sub u-mt-12">Cost per lead is the monthly spend divided by the leads that source brought in over the last 30 days; cost per customer divides by those that became customers.</p>
    </Card>
  );
}

export default function Insights({ range, embedded = false }) {
  useSourceRegistry();
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); (range ? ops.insights(range) : api.insights()).then(setD).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message="Insights could not load." detail={error} onRetry={load} />;
  if (!d) return <div className="an-skel" aria-busy="true"><div className="an-skel-tiles">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={96} />)}</div><Skeleton height={220} /></div>;
  const o = d.outreach;
  const maxSector = Math.max(1, ...d.by_sector.map((s) => s.leads));
  const dl = <DownloadButton path={exportPath("insights")} label="Download insights" testId="download-insights"
    title="Every number on this page as one table: section, item, measure, value" />;

  return (
    <>
      {embedded ? (
        <div className="an-toolbar u-between">
          <p className="sub u-m0">{range ? "Leads created and emails sent in the chosen period. " : ""}What is working. Rates are of leads we have emailed.</p>
          {dl}
        </div>
      ) : <PageHeader title="Insights" description={`${range ? "Leads created and emails sent in the chosen period. " : ""}What is working. Rates are of leads we have emailed.`} actions={dl} />}
      <Stats>
        <Stat value={d.totals.leads} label="Leads" icon="users" tone="blue" />
        <Stat value={d.totals.emails_sent} label="Emails sent" icon="send" />
        <Stat value={`${o.reply_rate}%`} label="Reply rate" icon="inbox" tone="violet" />
        <Stat value={o.interested} label="Interested or further" icon="star" tone="amber" />
        <Stat value={`${o.unsubscribe_rate}%`} label="Unsubscribe rate" icon="close" />
        <Stat value={d.totals.whatsapp_sent} label="WhatsApp sent" icon="send" />
        <Stat value={d.totals.customers} label="Customers" icon="trophy" tone="green" />
        <Stat value={`${d.by_band.hot} / ${d.by_band.warm} / ${d.by_band.cold}`} label="Hot / warm / cold" icon="zap" />
      </Stats>

      <div className="an-grid">
        <Card className="an-card-table">
          <h3>By lead source</h3>
          {d.by_source.length === 0 ? <EmptyState icon="database" title="No data yet">Sources show up once leads are imported or found.</EmptyState> : (
            <DataTable label="By lead source">
              <thead><tr><th>Source</th><th className="num">Leads</th><th className="num">Replied</th><th className="num">Interested</th><th className="num">Customers</th><th className="num">Cost per lead</th><th className="num">Cost per customer</th></tr></thead>
              <tbody>{d.by_source.map((s) => (
                <tr key={s.source}><td>{sourceLabel(s.source)}</td><td className="num">{s.leads}</td><td className="num">{s.replied}</td><td className="num">{s.interested}</td><td className="num">{s.customers}</td>
                  <td className="num">{money(s.cost_per_lead)}</td><td className="num">{money(s.cost_per_customer)}</td></tr>
              ))}</tbody>
            </DataTable>
          )}
        </Card>
        <Card className="an-card-table" data-testid="stage-by-source">
          <h3>Stage by source</h3>
          {d.by_source.length === 0 ? <EmptyState icon="database" title="No data yet" /> : (
            <DataTable label="Stage by source">
              <thead><tr><th>Source</th>{STAGE_COLUMNS.map((st) => <th key={st} className="num">{stageLabel(st)}</th>)}</tr></thead>
              <tbody>{d.by_source.map((s) => (
                <tr key={s.source}><td>{sourceLabel(s.source)}</td>{STAGE_COLUMNS.map((st) => <td key={st} className="num">{s.stages?.[st] || 0}</td>)}</tr>
              ))}</tbody>
            </DataTable>
          )}
        </Card>
        <Card>
          <CardHeader title="By sector" />
          {d.by_sector.length === 0 && <EmptyState icon="building" title="No data yet" />}
          {d.by_sector.map((s) => (
            <div className="funnel-row" key={s.sector}>
              <span>{s.sector}</span>
              <div className="bar" style={{ width: `${(s.leads / maxSector) * 100}%` }} />
              <b>{s.leads}</b>
            </div>
          ))}
        </Card>
        <Card className="an-card-table">
          <h3>Top leads right now</h3>
          {d.top_leads.length === 0 ? <EmptyState icon="users" title="No leads yet" /> : (
            <DataTable label="Top leads">
              <thead><tr><th>Company</th><th className="num">Score</th><th>Stage</th></tr></thead>
              <tbody>{d.top_leads.map((l) => <tr key={l.id}><td>{l.company}</td><td className="num">{l.score}</td><td>{l.stage}</td></tr>)}</tbody>
            </DataTable>
          )}
        </Card>
      </div>
      {isOwner() && d.by_source.length > 0 && <SourceSpend rows={d.by_source} saved={d.source_spend || {}} onSaved={load} />}
    </>
  );
}
