import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import { sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import PeriodPicker, { DeltaTile, usePeriod } from "../PeriodPicker.jsx";
import Intelligence from "./Intelligence.jsx";
import Insights from "./Insights.jsx";
import Pipeline from "./Pipeline.jsx";
import HelpLink from "../components/HelpLink.jsx";
import Abbr from "../components/Abbr.jsx";
import { PageHeader } from "../ui/index.js";

// One page for the numbers: Pipeline (where every lead stands), Overview, Funnel, Channels (was Intelligence), Sources (was Insights) and Team, all for one period.
const TABS = [["pipeline", "Pipeline"], ["overview", "Overview"], ["funnel", "Funnel"], ["channels", "Channels"], ["sources", "Sources"], ["team", "Team"]];
const STEP_LABEL = { contacted: "Contacted", replied: "Replied", interested: "Interested", meeting: "Meeting", customer: "Customer" };
const GROUP_LABEL = { campaign: "Campaign", step: "Sequence step", variant: "Template variant", source: "Lead source" };
const GRID = { display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 160px), 1fr))" };

function tabFromHash() {
  const m = /[?&]tab=([a-z]+)/.exec(window.location.hash);
  return m && TABS.some(([id]) => id === m[1]) ? m[1] : "pipeline";
}

function Overview({ data }) {
  const k = data.overview;
  return (
    <>
      <div style={GRID}>
        <DeltaTile label="New leads" kpi={k.new_leads} tone="tone-blue" />
        <DeltaTile label="Contacted" kpi={k.contacted} />
        <DeltaTile label="Emails sent" kpi={k.emails_sent} />
        <DeltaTile label="Open rate" kpi={k.open_rate} suffix="%" />
        <DeltaTile label="Replied" kpi={k.replied} tone="tone-violet" />
        <DeltaTile label="Reply rate" kpi={k.reply_rate} suffix="%" />
        <DeltaTile label="Interested" kpi={k.interested} tone="tone-amber" />
        <DeltaTile label="Meetings" kpi={k.meetings} />
        <DeltaTile label="Customers" kpi={k.customers} tone="tone-green" />
        <DeltaTile label="Unsubscribed" kpi={k.unsubscribed} lowerIsBetter />
      </div>
      <p className="sub" style={{ marginTop: 10 }}>Counts are of things that happened in the period (a lead counts once per step). Opens are counted for leads emailed in the period.</p>
    </>
  );
}

function FunnelBars({ row, steps }) {
  const max = Math.max(1, row.contacted);
  return steps.map((s) => (
    <div className="funnel-row" key={s}>
      <span>{STEP_LABEL[s]}</span>
      <div className="bar" role="img" aria-label={`${STEP_LABEL[s]}: ${row[s]}`} style={{ width: `${(row[s] / max) * 100}%` }} />
      <b>{row[s]}</b>{s !== "contacted" && <small className="sub-inline"> {row.rates[s]}%</small>}
    </div>
  ));
}

function Funnel({ data }) {
  const [group, setGroup] = useState("campaign");
  const f = data.funnel;
  const rows = f.by[group] || [];
  const name = (r) => (group === "source" ? sourceLabel(r.name) : r.name);
  return (
    <>
      <div className="grid g2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Funnel</h3>
          <p className="sub">Leads first contacted in this period, and how far they have got by now. Rates are of those contacted.</p>
          {f.overall.contacted === 0 ? <p className="empty">Nobody was first contacted in this period.</p> : <FunnelBars row={f.overall} steps={f.steps} />}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Stage velocity</h3>
          <p className="sub">Median days between steps, for leads that reached the later step in this period.</p>
          <table><thead><tr><th>Step</th><th>Median days</th><th>Leads</th></tr></thead>
            <tbody>{f.velocity.steps.map((s) => <tr key={s.to}><td>{STEP_LABEL[s.from]} → {STEP_LABEL[s.to]}</td><td>{s.median_days ?? "—"}</td><td>{s.samples}</td></tr>)}</tbody></table>
          {f.velocity.stages.length > 0 && (
            <>
              <h4>Days spent in each stage</h4>
              <table><thead><tr><th>Stage</th><th>Median days</th><th>Moves</th></tr></thead>
                <tbody>{f.velocity.stages.map((s) => <tr key={s.stage}><td>{stageLabel(s.stage)}</td><td>{s.median_days ?? "—"}</td><td>{s.samples}</td></tr>)}</tbody></table>
            </>
          )}
        </div>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>Funnel by {GROUP_LABEL[group].toLowerCase()}</h3>
          <label className="row" style={{ gap: 6, margin: 0 }}>Group by
            <select value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Group the funnel by" style={{ width: "auto" }}>
              {Object.entries(GROUP_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        </div>
        {group !== "campaign" && group !== "source" && <p className="sub">Each lead counts under the email that got its reply (or its latest email).</p>}
        {rows.length === 0 ? <p className="empty">No data for this period.</p> : (
          <div className="table-scroll"><table data-testid="funnel-by-group">
            <thead><tr><th>{GROUP_LABEL[group]}</th>{f.steps.map((s) => <th key={s}>{STEP_LABEL[s]}</th>)}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.name}><td><b>{name(r)}</b></td>{f.steps.map((s) => <td key={s}>{r[s]}{s !== "contacted" && <small className="sub-inline"> {r.rates[s]}%</small>}</td>)}</tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </>
  );
}

function Team({ data }) {
  return (
    <div className="card table-card" style={{ padding: 0 }}>
      {data.team.length === 0 ? <p className="empty">Nobody did anything with their name on it in this period.</p> : (
        <div className="table-scroll"><table data-testid="team-table">
          <thead><tr><th>Person</th><th>Actions</th><th>Stage moves</th><th>Calls</th><th>Notes</th><th>Other</th></tr></thead>
          <tbody>{data.team.map((m) => (
            <tr key={m.who}><td><b>{m.who}</b></td><td>{m.total}</td><td>{m.stage_changes}</td><td>{m.calls}</td><td>{m.notes}</td>
              <td><small>{Object.entries(m.by_kind).filter(([k]) => !["stage", "call", "note"].includes(k)).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "—"}</small></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export default function Analytics() {
  useSourceRegistry();
  const [tab, setTab] = useState(tabFromHash);
  const [period, setPeriod] = usePeriod();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); ops.analytics(period).then(setData).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, [JSON.stringify(period)]);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const pick = (id) => {
    setTab(id);
    const base = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "analytics";
    window.history.replaceState(null, "", `#${base}?tab=${id}`);
  };
  return (
    <>
      <PageHeader title="Analytics" help="analytics" helpLabel="Analytics"
        description={<>What is working, for the period you pick. All days and times are India Standard Time (<Abbr term="IST">IST</Abbr>).</>} />
      <PeriodPicker value={period} onChange={setPeriod} range={data?.range} />
      <div className="tabs scroll" role="tablist" aria-label="Analytics sections">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => pick(id)}>{label}</button>
        ))}
      </div>
      {error && <ErrorState message="Analytics could not load." detail={error} onRetry={load} />}
      {tab === "channels" && <Intelligence key={JSON.stringify(period)} range={period} embedded />}
      {tab === "pipeline" && <Pipeline period={period} />}
      {tab === "sources" && <Insights key={JSON.stringify(period)} range={period} embedded />}
      {["overview", "funnel", "team"].includes(tab) && !error && (!data ? <p className="empty">Loading…</p> : (
        <>
          {tab === "overview" && <Overview data={data} />}
          {tab === "funnel" && <Funnel data={data} />}
          {tab === "team" && <Team data={data} />}
        </>
      ))}
    </>
  );
}
