// Dependency-free SVG / CSS charts for the sales funnel page. They keep the same elements between refreshes, so a new
// number only moves a bar (CSS transition) instead of redrawing the chart.
import { inrShort, pct as pctText, STAGE_COLORS } from "./salesFormat.js";
import { Stage } from "./components/Abbr.jsx";

// The 8-stage funnel: one bar per stage, widths follow the counts; the conversion from the stage before sits beside it.
export function FunnelChart({ stages, onPick }) {
  const max = Math.max(1, ...stages.map((s) => s.count));
  return (
    <div className="sf-funnel" data-testid="sf-funnel" role="list">
      {stages.map((s, i) => (
        <div key={s.key} className="sf-funnel-row" role="listitem">
          <span className="sf-funnel-label"><Stage status={s.label} full /></span>
          <button type="button" className="sf-funnel-bar" onClick={() => onPick(s.drill)} disabled={!s.count} data-testid={`sf-stage-${s.key}`}
                  aria-label={`${s.label}: ${s.count} leads${i ? `, ${pctText(s.conv)} of the stage before` : ""}. Show the leads.`}>
            <span className="sf-funnel-fill" style={{ width: `${Math.max(s.count ? 3 : 0, (s.count / max) * 100)}%`, background: STAGE_COLORS[i] }} />
            <b>{s.count}</b>
          </button>
          <small className="sf-funnel-conv" title="Of the stage before">{i === 0 ? "" : pctText(s.conv)}</small>
        </div>
      ))}
    </div>
  );
}

// Month-wise bars (closed value by Closed Date).
export function ColumnChart({ rows, format = inrShort, height = 190, label = "Value by month" }) {
  const width = 560, pad = { l: 56, r: 8, t: 12, b: 28 };
  const max = Math.max(1, ...rows.map((r) => r.value));
  const slot = (width - pad.l - pad.r) / Math.max(1, rows.length);
  const y = (v) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" data-testid="sf-months"
         aria-label={`${label}: ${rows.map((r) => `${r.label} ${format(r.value)}`).join(", ") || "no data"}`}>
      {[0, 0.5, 1].map((t) => (
        <g key={t}><line x1={pad.l} x2={width - pad.r} y1={y(max * t)} y2={y(max * t)} stroke="var(--line)" />
          <text x={pad.l - 6} y={y(max * t) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{format(Math.round(max * t))}</text></g>
      ))}
      {rows.map((r, i) => {
        const w = Math.min(46, slot * 0.6), x = pad.l + slot * i + (slot - w) / 2;
        return (
          <g key={r.key || r.label}>
            <rect x={x} y={y(r.value)} width={w} height={Math.max(1, height - pad.b - y(r.value))} rx="4" fill="var(--brand)" style={{ transition: "y .4s, height .4s" }}>
              <title>{`${r.label}: ${format(r.value)}`}</title></rect>
            <text x={x + w / 2} y={height - 10} textAnchor="middle" fontSize="10" fill="var(--muted)">{r.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

// Half-circle gauge for a percentage (SLA compliance).
export function Gauge({ value, caption }) {
  const r = 70, c = Math.PI * r, v = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  const color = v >= 80 ? "var(--ok)" : v >= 50 ? "#d97706" : "#dc2626";
  return (
    <div className="sf-gauge" data-testid="sf-gauge">
      <svg viewBox="0 0 180 104" width="100%" role="img" aria-label={`${caption}: ${value === null || value === undefined ? "no data" : `${value}%`}`}>
        <path d="M20 90 A70 70 0 0 1 160 90" fill="none" stroke="var(--line)" strokeWidth="16" strokeLinecap="round" />
        <path d="M20 90 A70 70 0 0 1 160 90" fill="none" stroke={color} strokeWidth="16" strokeLinecap="round"
              strokeDasharray={`${(v / 100) * c} ${c}`} style={{ transition: "stroke-dasharray .5s" }} />
        <text x="90" y="82" textAnchor="middle" fontSize="26" fontWeight="800" fill="var(--ink)">{value === null || value === undefined ? "—" : `${value}%`}</text>
      </svg>
      <small className="sub">{caption}</small>
    </div>
  );
}

// Two bars per row: total value and probability-weighted value.
export function PairBars({ rows, onPick }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="sf-pairs" data-testid="sf-pipeline">
      {rows.map((r, i) => (
        <button type="button" key={r.key} className="sf-pair" onClick={() => onPick(r.drill)} disabled={!r.count}
                aria-label={`${r.label}: ${r.count} open leads, ${inrShort(r.value)}, weighted ${inrShort(r.weighted)}`}>
          <span className="sf-pair-label"><Stage status={r.label} full plain /> <small>({r.count})</small></span>
          <span className="sf-pair-bars">
            <span className="sf-pair-fill" style={{ width: `${(r.value / max) * 100}%`, background: STAGE_COLORS[i] }} />
            <span className="sf-pair-fill weighted" style={{ width: `${(r.weighted / max) * 100}%` }} />
          </span>
          <span className="sf-pair-num"><b>{inrShort(r.value)}</b><small>weighted {inrShort(r.weighted)}</small></span>
        </button>
      ))}
      <small className="sub"><i className="sf-key" style={{ background: "var(--brand)" }} /> value <i className="sf-key weighted" /> weighted by the stage probability</small>
    </div>
  );
}
