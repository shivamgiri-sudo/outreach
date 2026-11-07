// Small dependency-free SVG charts: horizontal bars, a donut and a daily trend line. Colours work in light and dark.
export const SERIES = { blue: "#0284c7", green: "#16a34a", red: "#dc2626", amber: "#d97706", violet: "#7c3aed", slate: "#64748b", teal: "#0d9488" };

export function HBars({ rows, onPick, testid }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="hbars" data-testid={testid}>
      {rows.map((r) => (
        <button type="button" key={r.key} className="hbar-row" onClick={onPick ? () => onPick(r.key) : undefined} disabled={!onPick || !r.value}
                aria-label={`${r.label}: ${r.value}${r.note ? `, ${r.note}` : ""}`}>
          <span className="hbar-label">{r.label}</span>
          <span className="hbar-track"><span className="hbar-fill" style={{ width: `${(r.value / max) * 100}%`, background: r.color || SERIES.blue }} /></span>
          <b>{r.value}</b>{r.note && <small className="sub-inline">{r.note}</small>}
        </button>
      ))}
    </div>
  );
}

export function Donut({ parts, size = 150, label, onPick }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  const r = size / 2 - 12, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${parts.map((p) => `${p.label} ${p.value}`).join(", ")}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth="18" />
        {total > 0 && parts.filter((p) => p.value).map((p) => {
          const len = (p.value / total) * c;
          const el = <circle key={p.key} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={p.color} strokeWidth="18"
                             strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`} />;
          offset += len;
          return el;
        })}
        <text x="50%" y="48%" textAnchor="middle" fontSize="22" fontWeight="700" fill="var(--ink)">{total}</text>
        <text x="50%" y="62%" textAnchor="middle" fontSize="11" fill="var(--muted)">{label}</text>
      </svg>
      <ul className="legend">
        {parts.map((p) => (
          <li key={p.key}><button type="button" className="link" onClick={onPick ? () => onPick(p.key) : undefined} disabled={!onPick || !p.value}>
            <i style={{ background: p.color }} />{p.label}: <b>{p.value}</b>{total ? ` (${Math.round((100 * p.value) / total)}%)` : ""}
          </button></li>
        ))}
      </ul>
    </div>
  );
}

export function TrendChart({ days, series, height = 180 }) {
  const width = 640, pad = { l: 30, r: 8, t: 10, b: 22 };
  const max = Math.max(1, ...days.flatMap((d) => series.map((s) => d[s.key] || 0)));
  const x = (i) => pad.l + (days.length < 2 ? 0 : (i / (days.length - 1)) * (width - pad.l - pad.r));
  const y = (v) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  const ticks = [0, Math.round(max / 2), max];
  const every = Math.max(1, Math.ceil(days.length / 8));
  const totals = series.map((s) => `${s.label} ${days.reduce((n, d) => n + (d[s.key] || 0), 0)}`).join(", ");
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`Daily trend over ${days.length} days: ${totals}`} data-testid="trend-chart">
        {ticks.map((t) => <g key={t}><line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" />
          <text x={pad.l - 4} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{t}</text></g>)}
        {days.map((d, i) => i % every === 0 && <text key={d.day} x={x(i)} y={height - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">{d.day.slice(5)}</text>)}
        {series.map((s) => (
          <g key={s.key}>
            <polyline fill="none" stroke={s.color} strokeWidth="2" points={days.map((d, i) => `${x(i)},${y(d[s.key] || 0)}`).join(" ")} />
            {days.map((d, i) => d[s.key] ? <circle key={i} cx={x(i)} cy={y(d[s.key])} r="2.5" fill={s.color}><title>{`${d.day} ${s.label}: ${d[s.key]}`}</title></circle> : null)}
          </g>
        ))}
      </svg>
      <ul className="legend row">{series.map((s) => <li key={s.key}><i style={{ background: s.color }} />{s.label}</li>)}</ul>
    </div>
  );
}
