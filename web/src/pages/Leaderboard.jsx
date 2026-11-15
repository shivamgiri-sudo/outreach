import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";
import { PageHeader, Card, DataTable, EmptyState, Skeleton } from "../ui/index.js";
import "../inbox-pages.css";

const PERIODS = [["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week"], ["month", "This month"]];
const MAX_DAYS = 92;
const istDay = (offset = 0) => new Date(Date.now() + 5.5 * 3600e3 + offset * 864e5).toISOString().slice(0, 10);
// The same sentences the API answers with (app/leaderboard.py), so a mistake reads the same before and after the request.
function rangeProblem(from, to) {
  if (!from || !to) return "A custom period needs both a from and a to date.";
  if (from > to) return "The from date is after the to date.";
  if ((Date.parse(to) - Date.parse(from)) / 864e5 + 1 > MAX_DAYS) return `A period can be at most ${MAX_DAYS} days.`;
  return "";
}
const MEASURES = [["calls", "Calls"], ["connected", "Connected"], ["interested", "Interested"], ["meetings", "Meetings"]];
const shown = (n) => (n == null ? "No target" : n > 999 ? "999%+" : `${n}%`);
const delta = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "no change");

function Attain({ value, calls, target }) {
  if (value == null) return <span className="muted">No target</span>;
  return (
    <div className="lb-attain">
      <div className="lb-track" role="img" aria-label={`${shown(value)} of target`}><div className="lb-fill" style={{ width: `${Math.min(100, value)}%` }} /></div>
      <span className="u-nowrap"><b>{shown(value)}</b> <span className="muted">({calls}/{target})</span></span>
    </div>
  );
}

export default function Leaderboard() {
  const [period, setPeriod] = useState("today");
  const [rankBy, setRankBy] = useState("calls");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [from, setFrom] = useState(() => istDay(-6));
  const [to, setTo] = useState(() => istDay());
  const [rangeErr, setRangeErr] = useState("");
  const problem = period === "custom" ? rangeProblem(from, to) : "";

  const load = () => {
    if (problem) { setRangeErr(problem); return Promise.resolve(); }
    setRangeErr("");
    return (period === "custom" ? api.leaderboard(period, rankBy, from, to) : api.leaderboard(period, rankBy))
      .then((d) => { setData(d); setError(""); })
      .catch((e) => (period === "custom" ? setRangeErr(e.message) : setError(e.message)));
  };
  useEffect(() => { setData(null); load(); }, [period, rankBy, from, to]);

  if (error && !data) return <ErrorState message="Could not load the leaderboard" detail={error} onRetry={load} />;
  const rows = data?.rows || [];
  const t = data?.totals;
  const prevLabel = data ? (data.previous.from === data.previous.to ? data.previous.from : `${data.previous.from} to ${data.previous.to}`) : "";

  return (
    <>
      <PageHeader title="Leaderboard"
        description="Who is calling, how many get through and how each person is doing against their daily target. Dates are IST. Marking a lead Invalid or Consultant is shown as Marked and does not count as a call." />

      <div className="filter-bar">
        <div className="tab-chips u-m0" role="group" aria-label="Period">
          {PERIODS.map(([id, text]) => (
            <button key={id} className={`tab-chip${period === id ? " on" : ""}`} aria-pressed={period === id} onClick={() => setPeriod(id)}>{text}</button>
          ))}
          <button className={`tab-chip${period === "custom" ? " on" : ""}`} aria-pressed={period === "custom"} data-testid="chip-custom" onClick={() => setPeriod("custom")}>Custom</button>
        </div>
        <label className="row u-gap-8 u-m0 push">Rank by
          <select aria-label="Rank by" value={rankBy} onChange={(e) => setRankBy(e.target.value)}>
            {MEASURES.map(([id, text]) => <option key={id} value={id}>{text}</option>)}
          </select>
        </label>
      </div>
      {period === "custom" && (
        <Card className="u-mb-12" data-testid="custom-range">
          <div className="date-pair">
            <label>From <input type="date" aria-label="From date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></label>
            <label>To <input type="date" aria-label="To date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></label>
            <span className="muted">IST days, up to {MAX_DAYS} days.</span>
          </div>
          {rangeErr && <div className="tip error u-mt-8" role="alert" data-testid="range-error">{rangeErr}</div>}
        </Card>
      )}
      {data && <p className="muted u-mb-12">{data.from === data.to ? data.from : `${data.from} to ${data.to}`} · {data.working_days} working day{data.working_days === 1 ? "" : "s"} (Mon to Sat)</p>}

      {!data && !error && !rangeErr && <Card><Skeleton height={20} style={{ marginBottom: 12 }} /><Skeleton height={20} style={{ marginBottom: 12 }} /><Skeleton height={20} /></Card>}
      {data && rows.length === 0 && (
        <Card><EmptyState icon="trophy" title="Nothing to rank yet">
          No calls were logged in this period, so there is nothing to rank. Calls appear here as soon as someone logs an outcome on the Call desk.</EmptyState></Card>
      )}
      {data && rows.length > 0 && (
        <Card className="table-card" data-testid="leaderboard">
          <DataTable>
              <thead>
                <tr><th>Rank</th><th>Caller</th><th className="num">Calls</th><th className="num">Connected</th><th className="num">Connect rate</th><th className="num">Interested</th><th className="num">Meetings</th><th className="num">Marked</th><th>Target reached</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.caller} data-testid={`row-${r.caller}`} className={r.you ? "lb-you" : ""}>
                    <td><span className={`lb-rank${r.rank === 1 ? " r1" : ""}`}>{r.rank}</span></td>
                    <td>{r.caller}{r.you ? " (you)" : ""}</td>
                    <td className="num">{r.calls} <span className="muted" title={`Previous period: ${r.calls_prev}`}>({delta(r.calls_delta)})</span></td>
                    <td className="num">{r.connected}</td>
                    <td className="num">{r.connect_rate}%</td>
                    <td className="num">{r.interested}</td>
                    <td className="num">{r.meetings}</td>
                    <td className="num">{r.marked}</td>
                    <td><Attain value={r.attainment} calls={r.calls} target={r.target} /></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr data-testid="row-total" className="lb-total">
                  <td></td><td>Team total</td>
                  <td className="num">{t.calls} <span className="muted">({delta(t.calls_delta)})</span></td>
                  <td className="num">{t.connected}</td><td className="num">{t.connect_rate}%</td><td className="num">{t.interested}</td><td className="num">{t.meetings}</td><td className="num">{t.marked}</td>
                  <td>{t.attainment == null ? "No target" : shown(t.attainment)}</td>
                </tr>
              </tfoot>
          </DataTable>
          <p className="muted u-m-12-16">The change in brackets compares calls with the {data.days} day{data.days === 1 ? "" : "s"} before ({prevLabel}, {t.calls_prev} calls). Targets are set on My day.</p>
        </Card>
      )}
    </>
  );
}
