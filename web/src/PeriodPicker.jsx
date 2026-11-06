import { useState } from "react";
import { istDate, istDateLabel } from "./time.js";

// The period the numbers cover: last 7 / 30 / 90 days or a custom from–to, in India days. Remembered per browser.
const KEY = "outreach-period";
export function loadPeriod() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (saved && (saved.days || (saved.from && saved.to))) return saved;
  } catch { /* storage blocked or bad value */ }
  return { days: 30 };
}
function savePeriod(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* storage blocked */ } }

export function usePeriod() {
  const [period, setPeriodState] = useState(loadPeriod);
  const setPeriod = (p) => { savePeriod(p); setPeriodState(p); };
  return [period, setPeriod];
}

const MAX_DAYS = 731;
const dayLabel = (d) => istDateLabel(`${d}T12:00:00+05:30`);
export function rangeText(range) {
  if (!range) return "";
  return `${dayLabel(range.from)} – ${dayLabel(range.to)} (IST), compared with ${dayLabel(range.prev_from)} – ${dayLabel(range.prev_to)}`;
}

export default function PeriodPicker({ value, onChange, range }) {
  const custom = !value.days;
  const [from, setFrom] = useState(value.from || istDate(-29));
  const [to, setTo] = useState(value.to || istDate(0));
  // The server takes at most 731 days (analytics_range.MAX_DAYS); say so here instead of a failed page load.
  const span = from && to ? Math.round((Date.parse(to) - Date.parse(from)) / 86400000) + 1 : 0;
  const tooLong = span > MAX_DAYS;
  const ok = from && to && from <= to && !tooLong;
  const apply = () => { if (ok) onChange({ from, to }); };
  return (
    <div className="card" style={{ marginBottom: 14, padding: 12 }} data-testid="period-picker">
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <b style={{ marginRight: 4 }}>Period</b>
        {[7, 30, 90].map((d) => (
          <button key={d} className={`btn ${value.days === d ? "" : "ghost"}`} aria-pressed={value.days === d} onClick={() => onChange({ days: d })}>Last {d} days</button>
        ))}
        <button className={`btn ${custom ? "" : "ghost"}`} aria-pressed={custom} onClick={() => onChange({ from, to })}>Custom</button>
        {custom && (
          <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <input type="date" aria-label="From (IST day)" value={from} max={to} onChange={(e) => setFrom(e.target.value)} style={{ width: "auto" }} />
            <span>to</span>
            <input type="date" aria-label="To (IST day)" value={to} min={from} max={istDate(0)} onChange={(e) => setTo(e.target.value)} style={{ width: "auto" }} />
            <button className="btn ghost" disabled={!ok} onClick={apply}>Apply</button>
            {tooLong && <small className="sub-inline" role="alert">Pick at most {MAX_DAYS} days (about two years).</small>}
          </span>
        )}
      </div>
      {range && <small className="sub-inline" style={{ display: "block", marginTop: 6 }}>{rangeText(range)}</small>}
    </div>
  );
}

// One KPI tile: the number, and how it moved against the previous period of the same length.
export function DeltaTile({ label, kpi, suffix = "", tone = "", lowerIsBetter = false }) {
  if (!kpi) return null;
  const up = kpi.delta > 0, down = kpi.delta < 0;
  const good = lowerIsBetter ? down : up, bad = lowerIsBetter ? up : down;
  const color = good ? "var(--ok, #15803d)" : bad ? "var(--hot, #be123c)" : "var(--muted)";
  const change = kpi.delta === 0 ? "no change" : `${up ? "▲" : "▼"} ${Math.abs(kpi.delta)}${suffix}${kpi.delta_pct != null ? ` (${up ? "+" : ""}${kpi.delta_pct}%)` : ""}`;
  return (
    <div className={`card stat ${tone}`} data-testid={`kpi-${label}`} style={{ minWidth: 0, overflowWrap: "anywhere" }}>
      <b>{kpi.value}{suffix}</b>
      <small style={{ color, fontWeight: 600, display: "block" }} aria-label={`Change against the previous period: ${change}`}>{change}</small>
      <span>{label}</span>
      <small style={{ display: "block", color: "var(--muted)" }}>before: {kpi.previous}{suffix}</small>
    </div>
  );
}
