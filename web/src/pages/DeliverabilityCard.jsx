import { useEffect, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { sourceId, sourceLabel, useSourceRegistry } from "../labels.js";

const pct = (x) => `${(x * 100).toFixed(1)}%`;
const VERDICT_COLOR = { good: "var(--ok)", warn: "var(--warn)", bad: "var(--mcn-red)", none: "var(--muted)" };
const VERDICT_TITLE = { good: "Healthy", warn: "Keep an eye on it", bad: "Too many bounces", none: "No data yet" };
const DNS_PILL = { ok: "ok", warn: "warn", fail: "warn", unknown: "" };
const DNS_WORD = { ok: "OK", warn: "Check", fail: "Fix", unknown: "Unknown" };

// The same label from different raw ids (aliases) is one row.
function mergeSources(rows) {
  const out = {};
  rows.forEach((r) => {
    const id = sourceId(r.key);
    const o = (out[id] = out[id] || { key: id, sent: 0, bounced: 0 });
    o.sent += r.sent; o.bounced += r.bounced;
  });
  return Object.values(out).map((o) => ({ ...o, label: sourceLabel(o.key), bounce_rate: o.sent ? o.bounced / o.sent : 0 }))
    .sort((a, b) => b.bounce_rate - a.bounce_rate || b.bounced - a.bounced);
}

function Table({ title, rows, labelOf = (r) => r.key, empty = "Nothing in this period." }) {
  return (
    <div className="mt-4">
      <b>{title}</b>
      {rows.length ? (
        <table>
          <thead><tr><th>{title.replace(/^By /, "")}</th><th>Sent</th><th>Bounced</th><th>Bounce rate</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.key}><td>{labelOf(r)}</td><td>{r.sent}</td><td>{r.bounced}</td><td>{r.sent ? pct(r.bounce_rate) : "-"}</td></tr>)}</tbody>
        </table>
      ) : <p className="sub m0">{empty}</p>}
    </div>
  );
}

// Day strip drawn to scale: tall bar = emails sent that day, red part (same scale) = bounced.
function DayStrip({ days }) {
  const W = 600, H = 70, top = 6, base = H - 4, n = days.length;
  const max = Math.max(1, ...days.map((d) => d.sent), ...days.map((d) => d.bounced));
  const slot = W / n, bw = Math.max(1, slot * 0.7);
  const h = (v) => (v / max) * (base - top);
  return (
    <div className="mt-4">
      <b>By day</b>
      <svg viewBox={`0 0 ${W} ${H + 14}`} width="100%" role="img" aria-label={`Emails sent and bounced per day, tallest day ${max}`} data-testid="deliv-strip" style={{ display: "block", maxWidth: 720 }}>
        <line x1="0" x2={W} y1={base} y2={base} stroke="var(--line)" />
        {days.map((d, i) => (
          <g key={d.day}>
            <title>{`${d.day}: ${d.sent} sent, ${d.bounced} bounced`}</title>
            <rect x={i * slot + (slot - bw) / 2} y={base - h(d.sent)} width={bw} height={h(d.sent)} fill="var(--brand-2)" />
            <rect x={i * slot + (slot - bw) / 2} y={base - h(d.bounced)} width={bw} height={h(d.bounced)} fill="var(--mcn-red)" />
          </g>
        ))}
        <text x="0" y={H + 10} fontSize="10" fill="var(--muted)">{days[0].day}</text>
        <text x={W} y={H + 10} fontSize="10" fill="var(--muted)" textAnchor="end">{days[n - 1].day}</text>
        <text x={W / 2} y={H + 10} fontSize="10" fill="var(--muted)" textAnchor="middle">tallest day: {max}</text>
      </svg>
      <p className="sub m0">Blue: emails sent. Red: bounced.</p>
    </div>
  );
}

function Dns() {
  const [dns, setDns] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { api.deliverabilityDns().then(setDns).catch((e) => setError(e.message)); }, []);
  if (error) return <div className="tip error mt-4" role="alert">{error}</div>;
  if (!dns) return <p className="sub mt-4">Looking up your sending domain...</p>;
  return (
    <div className="mt-5" data-testid="deliv-dns">
      <b>Sending domain records{dns.domain ? `: ${dns.domain}` : ""}</b>
      {!dns.configured ? <p className="sub m0">{dns.message}</p> : (
        <table>
          <thead><tr><th>Check</th><th>Result</th><th>What it means</th></tr></thead>
          <tbody>{dns.checks.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td><span className={`pill ${DNS_PILL[c.status]}`}>{DNS_WORD[c.status]}</span></td>
              <td>{c.advice}</td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

// "Which source bounces?": read-only report over the last 30 days, collapsed by default.
export default function DeliverabilityCard() {
  useSourceRegistry();
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const allowed = isOwner() || isWorkspaceAdmin();
  useEffect(() => { if (allowed && open) api.deliverability(days).then(setData).catch((e) => setError(e.message)); }, [allowed, open, days]);
  if (!allowed) return null;
  const v = data && data.verdict;
  return (
    <details className="card" data-testid="deliverability" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="card-summary">Deliverability: which source bounces</summary>
      {error && <div className="tip error mt-3" role="alert">{error}</div>}
      {open && !data && !error && <p className="sub">Loading...</p>}
      {data && (
        <div>
          <div className="tab-chips mt-3" role="group" aria-label="Period">
            {[7, 30, 90].map((d) => <button key={d} className={`tab-chip${days === d ? " on" : ""}`} onClick={() => setDays(d)}>{d} days</button>)}
          </div>
          <div style={{ borderLeft: `4px solid ${VERDICT_COLOR[v.level]}`, padding: "4px 12px", margin: "8px 0" }} data-testid="deliv-verdict" data-level={v.level}>
            <b style={{ color: VERDICT_COLOR[v.level] }}>{VERDICT_TITLE[v.level]}</b>
            <div>{v.text}</div>
          </div>
          <p className="my-2" data-testid="deliv-headline">
            Sent: <b>{data.sent}</b> · Delivered: <b>{data.delivered}</b> · Bounced: <b>{data.bounced}</b> ({pct(data.bounce_rate)}) · Spam complaints: <b>{data.complaints}</b> ({pct(data.complaint_rate)})
          </p>
          <p className="sub m0">
            {data.sending.paused ? `Sending is paused: ${data.sending.reason || "too many bounces"}` : "Sending is running."}{" "}
            It pauses by itself above {pct(data.sending.pause_above_bounce_rate)} bounces or {pct(data.sending.pause_above_complaint_rate)} spam complaints (needs {data.sending.min_sends_for_pause} sends in 7 days). Counts only: no addresses are shown here.
          </p>
          <DayStrip days={data.by_day} />
          <Table title="By source" rows={mergeSources(data.by_source)} labelOf={(r) => r.label} />
          <Table title="By domain" rows={data.by_domain} />
          <Table title="By email step" rows={data.by_step} empty="No emails sent yet." />
          <Table title="By email check" rows={data.by_check} />
          <Table title="By address type" rows={[...data.by_address_type, ...data.by_mailbox_type]} />
        </div>
      )}
      {open && <Dns />}
    </details>
  );
}
