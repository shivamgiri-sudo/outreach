// Sales funnel page, groups D–F: pipeline and forecast, attribution and people, closed revenue.
import { ColumnChart, PairBars } from "../salesCharts.jsx";
import { days, inr, inrShort, num, pct } from "../salesFormat.js";
import Abbr, { Stage } from "../components/Abbr.jsx";
import { Tiles } from "./SalesFunnelParts.jsx";
import { DataTable } from "../ui/index.js";

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
        <div className="card">
          <h4>Pipeline value by stage (open leads)</h4>
          <PairBars rows={data.pipeline} onPick={(k) => onDrill(k, "Open leads in this stage")} />
          <p className="sub u-mb-0">Open {inrShort(open)} · weighted {inrShort(weighted)}</p>
        </div>
        <div className="grid u-gap-16">
          <div className="card" data-testid="sf-coverage">
            <h4><Abbr term="Pipeline coverage">Pipeline coverage</Abbr></h4>
            <div className="sf-pair-nums"><div><b>{cov.ratio === null ? "—" : `${cov.ratio}×`}</b><small>pipeline ÷ target</small></div>
              <div><b>{inrShort(cov.pipeline)}</b><small>open pipeline</small></div><div><b>{cov.target ? inrShort(cov.target) : "—"}</b><small>target for these months</small></div></div>
            {!cov.target && <p className="sub">No target set for these months. Owners and workspace admins set monthly targets under Funnel settings below.</p>}
          </div>
          <div className="card" data-testid="sf-velocity">
            <h4><Abbr term="Sales velocity">Sales velocity</Abbr></h4>
            <div className="sf-pair-nums"><div><b>{v.perDay === null ? "—" : `${inrShort(v.perDay)}/day`}</b><small>expected revenue per day</small></div>
              <div><button type="button" className="linkish" disabled={!v.openOpportunities} onClick={() => onDrill(v.drill, "Open opportunities")}><b>{v.openOpportunities}</b></button><small>open opportunities</small></div>
              <div><b>{pct(v.winRate)}</b><small>win rate</small></div><div><b>{v.avgWonValue === null ? "—" : inrShort(v.avgWonValue)}</b><small>avg deal / month</small></div>
              <div><b>{days(v.avgCycleDays)}</b><small>avg sales cycle</small></div></div>
          </div>
        </div>
      </div>
      <div className="card u-mt-16" data-testid="sf-cycle">
        <h4>Average days per stage</h4>
        <div className="table-card u-xauto">
          <DataTable><thead><tr><th>Step</th><th className="u-right">Average days</th><th className="u-right">Leads measured</th></tr></thead>
            <tbody>{data.cycle.map((c) => <tr key={c.to}><td>{c.from} → {c.to}</td><td className="u-right"><b>{days(c.avgDays)}</b></td><td className="u-right">{c.measured}</td></tr>)}</tbody></DataTable>
        </div>
        <p className="sub u-mb-0">Stages that were back-filled (skipped when the lead was moved) are left out of the averages.</p>
      </div>
    </section>
  );
}

export function AttributionPeople({ data, onDrill }) {
  const g = data.scoreGrid, fits = Object.keys(g), engs = fits.length ? Object.keys(g[fits[0]]) : [];
  const maxCell = Math.max(1, ...fits.flatMap((f) => engs.map((e) => g[f][e].count)));
  return (
    <section aria-labelledby="sf-h-people">
      <h3 id="sf-h-people">Attribution and people</h3>
      <div className="card" data-testid="sf-sources">
        <h4>Source-wise funnel</h4>
        {data.sources.length === 0 ? <p className="empty">No leads in this range.</p> : (
          <div className="table-card u-xauto">
            <DataTable><thead><tr><th>Source</th>{data.funnel.map((s) => <th key={s.key} className="u-right"><Stage status={s.label} /></th>)}<th className="u-right">To <Abbr term="SQL">SQL</Abbr></th><th className="u-right">To Won</th><th className="u-right">Won value</th></tr></thead>
              <tbody>{data.sources.map((r) => (
                <tr key={r.source}><td><button type="button" className="linkish" onClick={() => onDrill(r.drill, `Source: ${r.source}`)}>{r.source}</button></td>
                  {r.counts.map((c, i) => <td key={i} className="u-right">{c}</td>)}<td className="u-right">{pct(r.toSql)}</td><td className="u-right">{pct(r.toWon)}</td><td className="u-right">{inrShort(r.wonValue)}</td></tr>
              ))}</tbody></DataTable>
          </div>
        )}
      </div>
      <div className="grid g2 u-mt-16">
        <div className="card" data-testid="sf-score-grid">
          <h4>Lead score: fit × engagement (open leads)</h4>
          <DataTable  tableClass="sf-grid"><thead><tr><th>Fit</th>{engs.map((e) => <th key={e} className="u-right">{e}</th>)}</tr></thead>
            <tbody>{fits.map((f) => (
              <tr key={f}><th scope="row">{f}</th>{engs.map((e) => (
                <td key={e} style={{ textAlign: "right", background: `color-mix(in srgb, var(--brand) ${Math.round((g[f][e].count / maxCell) * 40)}%, transparent)` }}>
                  <button type="button" className="linkish" disabled={!g[f][e].count} onClick={() => onDrill(g[f][e].drill, `Fit ${f}, ${e}`)} data-testid={`sf-cell-${f}-${e}`}>{g[f][e].count}</button></td>
              ))}</tr>
            ))}</tbody></DataTable>
        </div>
        <div className="card" data-testid="sf-recycled">
          <div className="row u-between"><h4 className="u-m0">Recycled and nurture pool</h4>
            <button type="button" className="linkish" disabled={!data.recycled.count} onClick={() => onDrill(data.recycled.drill, "Recycled and nurture leads")}>{data.recycled.count} leads</button></div>
          <DataTable><thead><tr><th>Waiting</th><th className="u-right">Leads</th></tr></thead>
            <tbody>{Object.entries(data.recycled.byAge).map(([k, n]) => <tr key={k}><td>{k}</td><td className="u-right">{n}</td></tr>)}</tbody></DataTable>
          {Object.keys(data.recycled.byReason).length > 0 && <p className="sub u-mb-0">Reasons: {Object.entries(data.recycled.byReason).map(([k, n]) => `${k} ${n}`).join(", ")}</p>}
        </div>
      </div>
      <div className="card u-mt-16" data-testid="sf-owners">
        <h4>Owner leaderboard</h4>
        {data.owners.length === 0 ? <p className="empty">No leads in this range.</p> : (
          <div className="table-card u-xauto">
            <DataTable><thead><tr><th>#</th><th>Owner</th><th className="u-right">Leads</th><th className="u-right">SQLs</th><th className="u-right">Meetings</th><th className="u-right">Proposals</th><th className="u-right">Won</th><th className="u-right">Win %</th><th className="u-right">Won value</th><th className="u-right">Avg response</th></tr></thead>
              <tbody>{data.owners.map((o, i) => (
                <tr key={o.owner}><td>{i + 1}</td><td><button type="button" className="linkish" onClick={() => onDrill(o.drill, `Owner: ${o.owner}`)}>{o.owner}</button></td>
                  <td className="u-right">{o.leads}</td><td className="u-right">{o.sqls}</td><td className="u-right">{o.meetings}</td><td className="u-right">{o.proposals}</td><td className="u-right">{o.won}</td>
                  <td className="u-right">{pct(o.winPct)}</td><td className="u-right">{inrShort(o.wonValue)}</td><td className="u-right">{o.avgSpeedHours === null ? "—" : `${o.avgSpeedHours} h`}</td></tr>
              ))}</tbody></DataTable>
          </div>
        )}
      </div>
    </section>
  );
}

function Breakdown({ title, rows, onDrill }) {
  return (
    <div className="card" data-testid={`sf-by-${title.toLowerCase()}`}>
      <h4>By {title.toLowerCase()}</h4>
      {rows.length === 0 ? <p className="empty">No deals closed in this range.</p> : (
        <div className="table-card u-xauto">
          <DataTable><thead><tr><th>{title}</th><th className="u-right">Deals</th><th className="u-right">Estimated</th><th className="u-right">Actual</th><th className="u-right">Variance</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.name}><td><button type="button" className="linkish" onClick={() => onDrill(r.drill, `${title}: ${r.name}`)}>{r.name}</button></td><td className="u-right">{r.deals}</td>
                <td className="u-right">{inr(r.estValue)}</td><td className="u-right">{inr(r.closedValue)}</td>
                <td className="u-right" className={r.variancePct === null ? "" : r.variancePct >= 0 ? "sf-up" : "sf-down"}>{r.variancePct === null ? "—" : `${r.variancePct > 0 ? "+" : ""}${r.variancePct}%`}</td></tr>
            ))}</tbody></DataTable>
        </div>
      )}
    </div>
  );
}

export function ClosedRevenue({ data, onDrill }) {
  const rev = data.revenue;
  return (
    <section aria-labelledby="sf-h-rev">
      <h3 id="sf-h-rev">Closed revenue</h3>
      <p className="sub">By the Closed Date (when the deal was won), not when the lead was created. <Abbr term="TCV">TCV</Abbr> is Total Contract Value: the monthly value times the months of the contract.</p>
      <Tiles tiles={rev.tiles} onDrill={onDrill} ids={["monthly", "tcv", "deals", "avg"]} testPrefix="-revenue" />
      <div className="card u-mt-16">
        <h4>Closed monthly value by month</h4>
        {rev.byMonth.length === 0 ? <p className="empty">No deals closed in this range.</p>
          : <ColumnChart rows={rev.byMonth.map((m) => ({ key: m.month, label: monthLabel(m.month), value: m.value }))} />}
      </div>
      <div className="grid g2 u-mt-16">
        <Breakdown title="Source" rows={rev.bySource} onDrill={onDrill} />
        <Breakdown title="Industry" rows={rev.byIndustry} onDrill={onDrill} />
        <Breakdown title="Service" rows={rev.byService} onDrill={onDrill} />
        <Breakdown title="Owner" rows={rev.byOwner} onDrill={onDrill} />
      </div>
    </section>
  );
}
