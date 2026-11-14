// Sales funnel (Lead Funnel dashboard): 18 components in six groups, for leads created in a date range (cohort basis).
// Polls every 60 s while the tab is visible and keeps the filters; the server answers "unchanged" when nothing moved.
import { useCallback, useEffect, useRef, useState } from "react";
import { sales } from "../salesApi.js";
import { istDate } from "../time.js";
import { ErrorState } from "../states.jsx";
import SalesDrill from "./SalesDrill.jsx";
import SalesSettings from "./SalesSettings.jsx";
import { FunnelHealth, SpeedDiscipline, StatusStrip, Tiles } from "./SalesFunnelParts.jsx";
import { AttributionPeople, ClosedRevenue, PipelineForecast } from "./SalesFunnelMore.jsx";
import HelpLink from "../components/HelpLink.jsx";
import CallTrackerFunnel from "./CallTrackerFunnel.jsx";
import "../funnel.css";

const POLL_MS = 60000;
const PRESET_NAMES = [["today", "Today"], ["week", "This week"], ["month", "This month"], ["lastMonth", "Last month"], ["quarter", "This quarter"], ["fy", "This FY"]];
const MULTI = [["source", "sources", "Lead source"], ["industry", "industries", "Industry"], ["service", "services", "Service line"], ["owner", "owners", "Lead owner"]];
const EMPTY = { from: "", to: "", source: [], industry: [], service: [], owner: [] };
const clock = () => new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

function MultiSelect({ label, options, value, onChange }) {
  const toggle = (o) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o]);
  return (
    <details className="multi-filter">
      <summary aria-label={`Filter by ${label.toLowerCase()}`}>{value.length ? `${label}: ${value.length}` : `All ${label.toLowerCase()}s`}</summary>
      <div className="multi-pop" role="group" aria-label={label}>
        {options.length === 0 && <small className="sub">Nothing to pick yet.</small>}
        {options.map((o) => <label key={o} className="row" style={{ margin: 0 }}><input type="checkbox" checked={value.includes(o)} onChange={() => toggle(o)} /> {o}</label>)}
        {value.length > 0 && <button type="button" className="linkish" onClick={() => onChange([])}>Clear</button>}
      </div>
    </details>
  );
}

function Controls({ f, setF, data, today, error }) {
  const bad = f.from && f.to && f.from > f.to ? "From Date cannot be after To Date" : "";
  const pick = (key, value) => setF({ ...f, [key]: value && value > today ? today : value });
  const preset = (name) => data?.presets?.[name] && setF({ ...f, ...data.presets[name] });
  const dirty = f.from || f.to || MULTI.some(([k]) => f[k].length);
  return (
    <div className="card sf-controls" data-testid="sf-filters">
      <div className="row" style={{ alignItems: "end" }}>
        <label>From<input type="date" value={f.from} max={f.to || today} onChange={(e) => pick("from", e.target.value)} data-testid="sf-from" /></label>
        <label>To<input type="date" value={f.to} max={today} onChange={(e) => pick("to", e.target.value)} data-testid="sf-to" /></label>
        <button type="button" className="btn ghost" onClick={() => setF({ ...EMPTY })} disabled={!dirty} data-testid="sf-reset">Reset</button>
      </div>
      <div className="row sf-presets" role="group" aria-label="Quick ranges">
        {PRESET_NAMES.map(([id, text]) => <button key={id} type="button" className="btn ghost" onClick={() => preset(id)} data-testid={`sf-preset-${id}`}>{text}</button>)}
      </div>
      <div className="row sf-multis">
        {MULTI.map(([k, listKey, label]) => <MultiSelect key={k} label={label} options={data?.filters?.[listKey] || []} value={f[k]} onChange={(v) => setF({ ...f, [k]: v })} />)}
      </div>
      {(bad || error) && <div className="tip error" role="alert" data-testid="sf-error">{bad || error}</div>}
    </div>
  );
}

export default function SalesFunnel() {
  const [f, setF] = useState({ ...EMPTY });
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState(false);
  const [refreshed, setRefreshed] = useState("");
  const [drill, setDrill] = useState(null);
  const stamp = useRef("");
  const seq = useRef(0);
  const today = data?.today || istDate(0);
  const invalid = Boolean(f.from && f.to && f.from > f.to);

  const load = useCallback(async (force) => {
    if (invalid) return;
    const mine = ++seq.current;
    try {
      const r = await sales.funnel(f, force ? "" : stamp.current);
      if (mine !== seq.current) return;
      setError(""); setFailed(false); setRefreshed(clock());
      if (r.unchanged) return;
      stamp.current = r.stamp;
      setData(r);
    } catch (e) {
      if (mine !== seq.current) return;
      if (data && !force) setFailed(true); else setError(e.message);
    }
  }, [f, invalid]);

  useEffect(() => { stamp.current = ""; load(true); }, [f]);     // a filter changed: fresh numbers
  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") load(false); };
    const timer = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [load]);

  const onDrill = (key, title) => key && setDrill({ key, title });
  const snapshotIds = ["inquiries", "mqls", "sqls", "opps", "won", "value"];
  return (
    <div data-testid="sales-funnel">
      <h2 style={{ marginBottom: 4 }}>Sales funnel <HelpLink chapter="funnel" label="Sales funnel" /></h2>
      <p className="sub">Leads by how far they got, from first inquiry to a signed deal. Counts are for leads created in the range; revenue is by the date the deal closed.</p>
      <Controls f={f} setF={setF} data={data} today={today} error={error && data ? error : ""} />
      {!data && error && <ErrorState message="The sales funnel could not load." detail={error} onRetry={() => load(true)} />}
      {!data && !error && <p className="sub" role="status">Loading the funnel…</p>}
      {data && (
        <>
          <StatusStrip data={data} refreshedAt={refreshed || clock()} failed={failed} />
          <Tiles tiles={data.snapshot} onDrill={onDrill} ids={snapshotIds} />
          <CallTrackerFunnel from={f.from} to={f.to} />
          <FunnelHealth data={data} onDrill={onDrill} />
          <SpeedDiscipline data={data} onDrill={onDrill} />
          <PipelineForecast data={data} onDrill={onDrill} />
          <AttributionPeople data={data} onDrill={onDrill} />
          <ClosedRevenue data={data} onDrill={onDrill} />
          <SalesSettings owners={data.filters.owners} onSaved={() => load(true)} />
        </>
      )}
      {drill && <SalesDrill drillKey={drill.key} title={drill.title} filters={f} onClose={() => setDrill(null)} />}
    </div>
  );
}
