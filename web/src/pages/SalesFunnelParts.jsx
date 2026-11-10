// Sales funnel page, groups A–C: the tiles, the funnel and its leakage, speed and discipline.
import { FunnelChart, Gauge } from "../salesCharts.jsx";
import { changeText, days, hours, inr, inrShort, num, pct } from "../salesFormat.js";
import Abbr, { Stage } from "../components/Abbr.jsx";

export function Tile({ label, value, change, previous, money, drill, onDrill, id }) {
  const c = changeText(change);
  const show = money ? inrShort(value) : num(value);
  return (
    <button type="button" className="card stat sf-tile" onClick={() => drill && onDrill(drill, label)} data-testid={`sf-tile-${id}`}
            aria-label={`${label}: ${money ? inr(value) : value}. Change against the previous period: ${c.text}. Show the leads.`}>
      <span className="sf-tile-label">{label}</span>
      <b>{show}</b>
      <small className={`sf-change ${c.tone}`}>{c.text}</small>
      <small className="sub-inline">before: {money ? inrShort(previous) : num(previous)}</small>
    </button>
  );
}

export function Tiles({ tiles, onDrill, ids, testPrefix = "" }) {
  return <div className="sf-tiles" data-testid={`sf-tiles${testPrefix}`}>{tiles.map((t, i) => <Tile key={t.label} {...t} id={ids[i]} onDrill={onDrill} />)}</div>;
}

export function StatusStrip({ data, refreshedAt, failed }) {
  return (
    <div className="sf-strip" data-testid="sf-strip" role="status">
      <span className={`sf-live ${failed ? "bad" : ""}`} aria-hidden="true" />
      <span>{failed ? "Could not refresh just now; showing the last numbers." : "Live"}</span>
      <span>Last refreshed <b data-testid="sf-refreshed">{refreshedAt}</b></span>
      <span><b>{num(data.status.updatedToday)}</b> record{data.status.updatedToday === 1 ? "" : "s"} updated today</span>
      <span data-testid="sf-range-label"><b>{data.range.label}</b></span>
    </div>
  );
}

export function FunnelHealth({ data, onDrill }) {
  const rows = data.leakage;
  const reasons = [...new Set(Object.values(rows).flatMap((r) => Object.keys(r)))];
  const cell = (stage, reason) => data.leakageCells.find((c) => c.stage === stage && c.reason === reason);
  return (
    <section aria-labelledby="sf-h-health">
      <h3 id="sf-h-health">Funnel health</h3>
      <div className="grid g2">
        <div className="card">
          <h4>Funnel (leads created in the range)</h4>
          <FunnelChart stages={data.funnel} onPick={(k) => onDrill(k, "Leads in this funnel stage")} />
          <p className="sub">Each bar is every lead that reached that stage by the To date. The % is of the stage before.</p>
        </div>
        <div className="card table-card-inner">
          <h4>Stage conversion</h4>
          <table data-testid="sf-conversions">
            <thead><tr><th>From → to</th><th style={{ textAlign: "right" }}>Count</th><th style={{ textAlign: "right" }}>Rate</th></tr></thead>
            <tbody>
              {data.conversions.map((c) => (
                <tr key={c.to}><td><Stage status={c.from} full /> → <Stage status={c.to.replace(" (overall)", "")} full />{c.to.includes("overall") && <small className="sub-inline"> overall</small>}</td>
                  <td style={{ textAlign: "right" }}>{num(c.fromCount)} → {num(c.toCount)}</td><td style={{ textAlign: "right" }}><b>{pct(c.rate)}</b></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h4 style={{ margin: 0 }}>Funnel leakage: Closed Lost by the stage it was lost at</h4>
          <button type="button" className="linkish" disabled={!data.lost.count} onClick={() => onDrill(data.lost.drill, "Closed Lost leads")} data-testid="sf-lost-total">{data.lost.count} lost</button>
        </div>
        {!reasons.length ? <p className="empty">No leads were lost in this range.</p> : (
          <div className="table-card" style={{ overflowX: "auto" }}>
            <table data-testid="sf-leakage">
              <thead><tr><th>Lost at stage</th>{reasons.map((r) => <th key={r} style={{ textAlign: "right" }}>{r}</th>)}</tr></thead>
              <tbody>
                {Object.keys(rows).map((stage) => (
                  <tr key={stage}><td><Stage status={stage} full /></td>
                    {reasons.map((r) => {
                      const c = cell(stage, r);
                      return <td key={r} style={{ textAlign: "right" }}>{c ? <button type="button" className="linkish" onClick={() => onDrill(c.drill, `Lost at ${statusShort(stage)}: ${r}`)}>{c.count}</button> : "—"}</td>;
                    })}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

export function SpeedDiscipline({ data, onDrill }) {
  const sp = data.speed, sla = data.sla, st = data.stalled;
  return (
    <section aria-labelledby="sf-h-speed">
      <h3 id="sf-h-speed">Speed and discipline</h3>
      <div className="grid g3">
        <div className="card" data-testid="sf-speed">
          <h4><Abbr term="Speed to lead">Speed to lead</Abbr></h4>
          <div className="sf-pair-nums"><div><b>{hours(sp.avgHours)}</b><small>average</small></div><div><b>{hours(sp.medianHours)}</b><small>median</small></div></div>
          <p className="sub">Created to first human contact. {sp.measured} lead{sp.measured === 1 ? "" : "s"} measured.</p>
          {sp.byOwner.length > 0 && (
            <table><thead><tr><th>Owner</th><th style={{ textAlign: "right" }}>Avg</th><th style={{ textAlign: "right" }}>Median</th></tr></thead>
              <tbody>{sp.byOwner.map((o) => <tr key={o.owner}><td>{o.owner}</td><td style={{ textAlign: "right" }}>{hours(o.avgHours)}</td><td style={{ textAlign: "right" }}>{hours(o.medianHours)}</td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">
          <h4><Abbr term="SLA">SLA</Abbr> compliance</h4>
          <Gauge value={sla.pct} caption={`First response within ${sla.hours} h: ${sla.within} of ${sla.measured}`} />
        </div>
        <div className="card" data-testid="sf-stalled">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h4 style={{ margin: 0 }}><Abbr term="Stalled lead">Stalled leads</Abbr></h4>
            <button type="button" className="linkish" disabled={!st.total} onClick={() => onDrill(st.drill, "Stalled leads")} data-testid="sf-stalled-total">{st.total} stalled</button>
          </div>
          <p className="sub">Open, no activity for more than the configured days; biggest value first.</p>
          {st.rows.length === 0 ? <p className="empty">Nothing is stalled.</p> : (
            <div style={{ maxHeight: 260, overflow: "auto" }}>
              <table><tbody>{st.rows.slice(0, 50).map((r) => (
                <tr key={r.id}><td><a href={`#leads?lead=${encodeURIComponent(r.id)}`}>{r.company || r.id}</a><br /><small className="sub-inline">{r.owner || "—"} · <Stage status={r.stage} /></small></td>
                  <td style={{ textAlign: "right" }}>{inr(r.value)}<br /><small className="sub-inline">{days(r.daysIdle)} idle</small></td></tr>
              ))}</tbody></table>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
