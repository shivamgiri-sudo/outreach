// Sales funnel page, groups D–F: pipeline and forecast, attribution and people, closed revenue.
import { ColumnChart, PairBars } from "../salesCharts.jsx";
import { days, inr, inrShort, num, pct } from "../salesFormat.js";
import Abbr, { Stage } from "../components/Abbr.jsx";
import { Tiles } from "./SalesFunnelParts.jsx";
import { Card, CardHeader, DataTable, EmptyState } from "../ui/index.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (m) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(2, 4)}`;

export function PipelineForecast({ data, onDrill }) {
  const cov = data.coverage, v = data.velocity;
  const open = data.pipeline.reduce((a, p) => a + p.value, 0);
  const weighted = data.pipeline.reduce((a, p) => a + p.weighted, 0);
  return (
    <section aria-labelledby="sf-h-pipe">
      <h3 id="sf-h-pipe">Pipeline and forecast</h3>
      <div className="grid g2">
        <Card>
          <CardHeader title="Pipeline value by stage (open leads)" />
          <PairBars rows={data.pipeline} onPick={(k) => onDrill(k, "Open leads in this stage")} />
          <p className="sub u-mb-0">Open {inrShort(open)} · weighted {inrShort(weighted)}</p>
        </Card>
        <div className="sf-stack">
          <Card data-testid="sf-coverage">
            <CardHeader title={<Abbr term="Pipeline coverage">Pipeline coverage</Abbr>} />
            <div className="sf-pair-nums"><div><b>{cov.ratio === null ? "—" : `${cov.ratio}×`}</b><small>pipeline ÷ target</small></div>
              <div><b>{inrShort(cov.pipeline)}</b><small>open pipeline</small></div><div><b>{cov.target ? inrShort(cov.target) : "—"}</b><small>target for these months</small></div></div>
            {!cov.target && <p className="sub">No target set for these months. Owners and workspace admins set monthly targets under Funnel settings below.</p>}
          </Card>
          <Card data-testid="sf-velocity">
            <CardHeader title={<Abbr term="Sales velocity">Sales velocity</Abbr>} />
            <div className="sf-pair-nums"><div><b>{v.perDay === null ? "—" : `${inrShort(v.perDay)}/day`}</b><small>expected revenue per day</small></div>
              <div><button type="button" className="linkish" disabled={!v.openOpportunities} onClick={() => onDrill(v.drill, "Open opportunities")}><b>{v.openOpportunities}</b></button><small>open opportunities</small></div>
              <div><b>{pct(v.winRate)}</b><small>win rate</small></div><div><b>{v.avgWonValue === null ? "—" : inrShort(v.avgWonValue)}</b><small>avg deal / month</small></div>
              <div><b>{days(v.avgCycleDays)}</b><small>avg sales cycle</small></div></div>
          </Card>
        </div>
      </div>
      <Card className="sf-section-gap" data-testid="sf-cycle">
        <CardHeader title="Average days per stage" />
        <div className="table-card u-xauto">
          <DataTable label="Average days per stage"><thead><tr><th>Step</th><th className="u-right">Average days</th><th className="u-right">Leads measured</th></tr></thead>
            <tbody>{data.cycle.map((c) => <tr key={c.to}><td>{c.from} → {c.to}</td><td className="u-right"><b>{days(c.avgDays)}</b></td><td className="u-right">{c.measured}</td></tr>)}</tbody></DataTable>
        </div>
        <p className="sub u-mb-0">Stages that were back-filled (skipped when the lead was moved) are left out of the averages.</p>
      </Card>
    </section>
  );
}

export function AttributionPeople({ data, onDrill }) {
  const g = data.scoreGrid, fits = Object.keys(g), engs = fits.length ? Object.keys(g[fits[0]]) : [];
  const maxCell = Math.max(1, ...fits.flatMap((f) => engs.map((e) => g[f][e].count)));
  return (
    <section aria-labelledby="sf-h-people">
      <h3 id="sf-h-people">Attribution and people</h3>
      <Card data-testid="sf-sources">
        <CardHeader title="Source-wise funnel" />
        {data.sources.length === 0 ? <EmptyState icon="database" title="No leads in this range">Pick a wider date range above.</EmptyState> : (
          <div className="table-card u-xauto">
            <DataTable label="Source-wise funnel"><thead><tr><th>Source</th>{data.funnel.map((s) => <th key={s.key} className="u-right"><Stage status={s.label} /></th>)}<th className="u-right">To <Abbr term="SQL">SQL</Abbr></th><th className="u-right">To Won</th><th className="u-right">Won value</th></tr></thead>
              <tbody>{data.sources.map((r) => (
                <tr key={r.source}><td><button type="button" className="linkish" onClick={() => onDrill(r.drill, `Source: ${r.source}`)}>{r.source}</button></td>
                  {r.counts.map((c, i) => <td key={i} className="u-right">{c}</td>)}<td className="u-right">{pct(r.toSql)}</td><td className="u-right">{pct(r.toWon)}</td><td className="u-right">{inrShort(r.wonValue)}</td></tr>
              ))}</tbody></DataTable>
          </div>
        )}
      </Card>
      <div className="grid g2 sf-section-gap">
        <Card data-testid="sf-score-grid">
          <CardHeader title="Lead score: fit × engagement (open leads)" />
          <DataTable label="Fit by engagement" tableClass="sf-grid"><thead><tr><th>Fit</th>{engs.map((e) => <th key={e} className="u-right">{e}</th>)}</tr></thead>
            <tbody>{fits.map((f) => (
              <tr key={f}><th scope="row">{f}</th>{engs.map((e) => (
                <td key={e} className="sf-heat" style={{ "--heat": `${Math.round((g[f][e].count / maxCell) * 40)}%` }}>
                  <button type="button" className="linkish" disabled={!g[f][e].count} onClick={() => onDrill(g[f][e].drill, `Fit ${f}, ${e}`)} data-testid={`sf-cell-${f}-${e}`}>{g[f][e].count}</button></td>
              ))}</tr>
            ))}</tbody></DataTable>
        </Card>
        <Card data-testid="sf-recycled">
          <CardHeader title="Recycled and nurture pool"
            actions={<button type="button" className="linkish" disabled={!data.recycled.count} onClick={() => onDrill(data.recycled.drill, "Recycled and nurture leads")}>{data.recycled.count} leads</button>} />
          <DataTable label="Recycled pool"><thead><tr><th>Waiting</th><th className="u-right">Leads</th></tr></thead>
            <tbody>{Object.entries(data.recycled.byAge).map(([k, n]) => <tr key={k}><td>{k}</td><td className="u-right">{n}</td></tr>)}</tbody></DataTable>
          {Object.keys(data.recycled.byReason).length > 0 && <p className="sub u-mb-0">Reasons: {Object.entries(data.recycled.byReason).map(([k, n]) => `${k} ${n}`).join(", ")}</p>}
        </Card>
      </div>
      <Card className="sf-section-gap" data-testid="sf-owners">
        <CardHeader title="Owner leaderboard" />
        {data.owners.length === 0 ? <EmptyState icon="users" title="No leads in this range">Pick a wider date range above.</EmptyState> : (
          <div className="table-card u-xauto">
            <DataTable label="Owner leaderboard"><thead><tr><th>#</th><th>Owner</th><th className="u-right">Leads</th><th className="u-right">SQLs</th><th className="u-right">Meetings</th><th className="u-right">Proposals</th><th className="u-right">Won</th><th className="u-right">Win %</th><th className="u-right">Won value</th><th className="u-right">Avg response</th></tr></thead>
              <tbody>{data.owners.map((o, i) => (
                <tr key={o.owner}><td>{i + 1}</td><td><button type="button" className="linkish" onClick={() => onDrill(o.drill, `Owner: ${o.owner}`)}>{o.owner}</button></td>
                  <td className="u-right">{o.leads}</td><td className="u-right">{o.sqls}</td><td className="u-right">{o.meetings}</td><td className="u-right">{o.proposals}</td><td className="u-right">{o.won}</td>
                  <td className="u-right">{pct(o.winPct)}</td><td className="u-right">{inrShort(o.wonValue)}</td><td className="u-right">{o.avgSpeedHours === null ? "—" : `${o.avgSpeedHours} h`}</td></tr>
              ))}</tbody></DataTable>
          </div>
        )}
      </Card>
    </section>
  );
}

function Breakdown({ title, rows, onDrill }) {
  return (
    <Card data-testid={`sf-by-${title.toLowerCase()}`}>
      <CardHeader title={`By ${title.toLowerCase()}`} />
      {rows.length === 0 ? <EmptyState icon="trophy" title="No deals closed in this range">Won deals appear here by their closed date.</EmptyState> : (
        <div className="table-card u-xauto">
          <DataTable label={`By ${title.toLowerCase()}`}><thead><tr><th>{title}</th><th className="u-right">Deals</th><th className="u-right">Estimated</th><th className="u-right">Actual</th><th className="u-right">Variance</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.name}><td><button type="button" className="linkish" onClick={() => onDrill(r.drill, `${title}: ${r.name}`)}>{r.name}</button></td><td className="u-right">{r.deals}</td>
                <td className="u-right">{inr(r.estValue)}</td><td className="u-right">{inr(r.closedValue)}</td>
                <td className={`u-right ${r.variancePct === null ? "" : r.variancePct >= 0 ? "sf-up" : "sf-down"}`}>{r.variancePct === null ? "—" : `${r.variancePct > 0 ? "+" : ""}${r.variancePct}%`}</td></tr>
            ))}</tbody></DataTable>
        </div>
      )}
    </Card>
  );
}

export function ClosedRevenue({ data, onDrill }) {
  const rev = data.revenue;
  return (
    <section aria-labelledby="sf-h-rev">
      <h3 id="sf-h-rev">Closed revenue</h3>
      <p className="sub">By the Closed Date (when the deal was won), not when the lead was created. <Abbr term="TCV">TCV</Abbr> is Total Contract Value: the monthly value times the months of the contract.</p>
      <Tiles tiles={rev.tiles} onDrill={onDrill} ids={["monthly", "tcv", "deals", "avg"]} testPrefix="-revenue" />
      <Card className="sf-section-gap">
        <CardHeader title="Closed monthly value by month" />
        {rev.byMonth.length === 0 ? <EmptyState icon="chart" title="No deals closed in this range">Won deals appear here by their closed date.</EmptyState>
          : <ColumnChart rows={rev.byMonth.map((m) => ({ key: m.month, label: monthLabel(m.month), value: m.value }))} />}
      </Card>
      <div className="sf-breakdowns">
        <Breakdown title="Source" rows={rev.bySource} onDrill={onDrill} />
        <Breakdown title="Industry" rows={rev.byIndustry} onDrill={onDrill} />
        <Breakdown title="Service" rows={rev.byService} onDrill={onDrill} />
        <Breakdown title="Owner" rows={rev.byOwner} onDrill={onDrill} />
      </div>
    </section>
  );
}
