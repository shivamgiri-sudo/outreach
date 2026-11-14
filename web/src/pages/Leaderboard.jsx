import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Empty, ErrorState } from "../states.jsx";

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
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 150 }}>
      <div role="img" aria-label={`${shown(value)} of target`} style={{ flex: 1, height: 8, background: "var(--line)", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(100, value)}%`, height: "100%", background: "var(--brand)" }} />
      </div>
      <span style={{ whiteSpace: "nowrap" }}><b>{shown(value)}</b> <span className="muted">({calls}/{target})</span></span>
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
      <h2>Leaderboard</h2>
      <p className="sub">Who is calling, how many get through and how each person is doing against their daily target. Dates are IST. Marking a lead Invalid or Consultant is shown as Marked and does not count as a call.</p>

      <div className="tab-chips" role="group" aria-label="Period">
        {PERIODS.map(([id, text]) => (
          <button key={id} className={`tab-chip${period === id ? " on" : ""}`} aria-pressed={period === id} onClick={() => setPeriod(id)}>{text}</button>
        ))}
        <button className={`tab-chip${period === "custom" ? " on" : ""}`} aria-pressed={period === "custom"} data-testid="chip-custom" onClick={() => setPeriod("custom")}>Custom</button>
      </div>
      {period === "custom" && (
        <div className="row" style={{ gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }} data-testid="custom-range">
          <label>From <input type="date" aria-label="From date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>To <input type="date" aria-label="To date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></label>
          <span className="muted">IST days, up to {MAX_DAYS} days.</span>
          {rangeErr && <div className="tip error" role="alert" data-testid="range-error">{rangeErr}</div>}
        </div>
      )}
      <div className="row" style={{ gap: 8, marginBottom: 12, alignItems: "center", flexWrap: "wrap" }}>
        <label>Rank by{" "}
          <select aria-label="Rank by" value={rankBy} onChange={(e) => setRankBy(e.target.value)}>
            {MEASURES.map(([id, text]) => <option key={id} value={id}>{text}</option>)}
          </select>
        </label>
        {data && <span className="muted">{data.from === data.to ? data.from : `${data.from} to ${data.to}`} · {data.working_days} working day{data.working_days === 1 ? "" : "s"} (Mon to Sat)</span>}
      </div>

      {!data && !error && !rangeErr && <p className="muted">Loading...</p>}
      {data && rows.length === 0 && (
        <Empty>No calls were logged in this period yet, so there is nothing to rank. Calls appear here as soon as someone logs an outcome on the Call desk.</Empty>
      )}
      {data && rows.length > 0 && (
        <div className="card" data-testid="leaderboard">
          <div className="table-wrap" style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr><th>Rank</th><th>Caller</th><th>Calls</th><th>Connected</th><th>Connect rate</th><th>Interested</th><th>Meetings</th><th>Marked</th><th>Target reached</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.caller} data-testid={`row-${r.caller}`} className={r.you ? "lb-you" : ""} style={r.you ? { background: "var(--brand-soft)", fontWeight: 600 } : undefined}>
                    <td>{r.rank}</td>
                    <td>{r.caller}{r.you ? " (you)" : ""}</td>
                    <td>{r.calls} <span className="muted" title={`Previous period: ${r.calls_prev}`}>({delta(r.calls_delta)})</span></td>
                    <td>{r.connected}</td>
                    <td>{r.connect_rate}%</td>
                    <td>{r.interested}</td>
                    <td>{r.meetings}</td>
                    <td>{r.marked}</td>
                    <td><Attain value={r.attainment} calls={r.calls} target={r.target} /></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr data-testid="row-total" style={{ fontWeight: 700 }}>
                  <td></td><td>Team total</td>
                  <td>{t.calls} <span className="muted">({delta(t.calls_delta)})</span></td>
                  <td>{t.connected}</td><td>{t.connect_rate}%</td><td>{t.interested}</td><td>{t.meetings}</td><td>{t.marked}</td>
                  <td>{t.attainment == null ? "No target" : shown(t.attainment)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>The change in brackets compares calls with the {data.days} day{data.days === 1 ? "" : "s"} before ({prevLabel}, {t.calls_prev} calls). Targets are set on My day.</p>
        </div>
      )}
    </>
  );
}
