import { useState } from "react";
import { istDate, istDateLabel } from "./time.js";
import "./analytics-pages.css";
import { Button, Card, Input, Segmented } from "./ui/index.js";

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
  const mode = custom ? "custom" : String(value.days);
  const pick = (id) => (id === "custom" ? onChange({ from, to }) : onChange({ days: Number(id) }));
  return (
    <Card className="an-period" data-testid="period-picker">
      <div className="an-period-row">
        <b>Period</b>
        <Segmented label="Period" value={mode} onChange={pick}
          items={[{ id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }, { id: "90", label: "Last 90 days" }, { id: "custom", label: "Custom" }]} />
        {custom && (
          <span className="an-period-custom">
            <Input type="date" aria-label="From (IST day)" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
            <span>to</span>
            <Input type="date" aria-label="To (IST day)" value={to} min={from} max={istDate(0)} onChange={(e) => setTo(e.target.value)} />
            <Button variant="secondary" disabled={!ok} onClick={apply}>Apply</Button>
            {tooLong && <small className="sub-inline" role="alert">Pick at most {MAX_DAYS} days (about two years).</small>}
          </span>
        )}
      </div>
      {range && <small className="an-range">{rangeText(range)}</small>}
    </Card>
  );
}

// One KPI tile: the number, and how it moved against the previous period of the same length.
export function DeltaTile({ label, kpi, suffix = "", tone = "", lowerIsBetter = false }) {
  if (!kpi) return null;
  const up = kpi.delta > 0, down = kpi.delta < 0;
  const good = lowerIsBetter ? down : up, bad = lowerIsBetter ? up : down;
  const change = kpi.delta === 0 ? "no change" : `${up ? "▲" : "▼"} ${Math.abs(kpi.delta)}${suffix}${kpi.delta_pct != null ? ` (${up ? "+" : ""}${kpi.delta_pct}%)` : ""}`;
  return (
    <div className={`card stat delta-tile ${tone}`} data-testid={`kpi-${label}`}>
      <b>{kpi.value}{suffix}</b>
      <small className={`delta ${good ? "good" : bad ? "bad" : ""}`} aria-label={`Change against the previous period: ${change}`}>{change}</small>
      <span>{label}</span>
      <small className="prev">before: {kpi.previous}{suffix}</small>
    </div>
  );
}
