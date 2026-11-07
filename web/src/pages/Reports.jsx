import { useEffect, useState } from "react";
import { getRole } from "../api.js";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import PeriodPicker, { usePeriod } from "../PeriodPicker.jsx";
import { HBars, SERIES, TrendChart } from "../charts.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import ResponseTimes from "./ResponseTimes.jsx";

// Reports: the daily report (what was done each day), each person's numbers, and the full activity log with the exact
// number / email / LinkedIn profile used. Download as CSV (owners and workspace admins).
const LABEL = { leads_added: "Leads added", calls: "Calls", calls_connected: "Calls connected", interested: "Interested", not_interested: "Not interested",
  follow_ups: "Follow-ups", bad_numbers: "Wrong / inactive numbers", emails_sent: "Emails sent", whatsapp_sent: "WhatsApp sent", linkedin: "LinkedIn steps",
  replies: "Replies received", meetings: "Meetings", notes: "Notes", stage_changes: "Stage changes", tasks_done: "Tasks done", opens: "Emails opened", clicks: "Links clicked" };
const KINDS = { "": "All actions", call: "Calls", email_sent: "Emails sent", whatsapp_sent: "WhatsApp sent", linkedin: "LinkedIn", reply: "Replies",
  stage: "Stage changes", note: "Notes", meeting: "Meetings", lead_added: "Leads added", task_done: "Tasks done", email_open: "Emails opened", email_click: "Links clicked" };
const DAY_COLS = ["leads_added", "calls", "calls_connected", "interested", "not_interested", "follow_ups", "emails_sent", "opens", "clicks", "whatsapp_sent", "linkedin", "replies", "meetings", "tasks_done"];
const pretty = (s) => String(s || "").replace(/_/g, " ");

function istDay(d) {
  return new Date(`${d}T00:00:00+05:30`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}

export default function Reports() {
  const [period, setPeriod] = usePeriod();
  const [who, setWho] = useState("");
  const [kind, setKind] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const [tab, setTab] = useState("daily");
  const [mailTo, setMailTo] = useState("");
  const [mailMsg, setMailMsg] = useState("");
  const [preview, setPreview] = useState("");
  const sendMail = async () => {
    setMailMsg("Sending…");
    try {
      const extra = mailTo.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
      const r = await ops.emailReport(period, who, kind, extra);
      const bad = Object.entries(r.results).filter(([, s]) => s !== "sent");
      setMailMsg(bad.length ? `Report ready, but not delivered to ${bad.map(([a, s]) => `${a} (${s === "dry_run" ? "no sending mailbox connected" : s})`).join(", ")}. The preview is below.`
        : `Report emailed to ${r.sent_to.join(", ")}.`);
      setPreview(r.html);
    } catch (e) { setMailMsg(e.message); }
  };
  const load = () => { setError(""); ops.dailyReport(period, who, kind).then(setData).catch((e) => setError(e.message)); };
  useEffect(load, [JSON.stringify(period), who, kind]);
  const canCsv = ["owner", "admin"].includes(getRole());
  const t = data?.totals || {};
  return (
    <>
      <h2>Reports</h2>
      <p className="sub">What was done each day and by whom. Times are India Standard Time. Every number comes from the activity log below.</p>
      <PeriodPicker value={period} onChange={setPeriod} range={data?.range} />
      <div className="row" style={{ gap: 10, flexWrap: "wrap", margin: "8px 0" }}>
        <label className="row" style={{ gap: 6, margin: 0 }}>Person
          <select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Person" data-testid="report-person" style={{ width: "auto" }}>
            <option value="">Everyone</option>
            {(data?.people || []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select></label>
        {canCsv && <button className="btn ghost" onClick={() => ops.dailyReportCsv(period, who, kind)} data-testid="report-csv">Download activity log (CSV)</button>}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", margin: "4px 0 10px" }}>
        {canCsv && <input value={mailTo} onChange={(e) => setMailTo(e.target.value)} placeholder="Also send to (emails, comma separated)" aria-label="Also send to" style={{ flex: "1 1 260px", minWidth: 0 }} />}
        <button className="btn" onClick={sendMail} data-testid="report-email">Email me this report</button>
        {mailMsg && <small className="sub-inline" role="status" data-testid="report-email-msg">{mailMsg}</small>}
      </div>
      {preview && (
        <details className="card" open style={{ marginBottom: 12 }}>
          <summary><b>Email preview</b> (as sent, with the CSV attached and a download link)</summary>
          <iframe title="Report email preview" srcDoc={preview} sandbox="" style={{ width: "100%", height: 520, border: "1px solid var(--line)", borderRadius: 8, background: "#fff" }} data-testid="report-preview" />
        </details>
      )}
      {error && <ErrorState message="The report could not load." detail={error} onRetry={load} />}
      {!data && !error && <p className="empty">Loading…</p>}
      {data && (
        <>
          <div className="stat-grid">
            {["leads_added", "calls", "calls_connected", "interested", "not_interested", "follow_ups", "emails_sent", "whatsapp_sent", "replies", "meetings"].map((k) => (
              <div key={k} className="card stat" data-testid={`rep-${k}`}><b>{t[k]}</b><span className="sub-inline">{LABEL[k]}</span></div>
            ))}
          </div>
          <div className="tabs scroll" role="tablist" aria-label="Report sections" style={{ marginTop: 14 }}>
            {[["daily", "Daily report"], ["team", "By person"], ["log", "Activity log"], ["sla", "Response times"]].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          {tab === "sla" && <ResponseTimes period={period} />}
          {tab === "daily" && (
            <>
              <div className="card"><h3 style={{ marginTop: 0 }}>Each day</h3>
                <TrendChart days={[...data.days].reverse()} series={[{ key: "calls", label: "Calls", color: SERIES.blue }, { key: "calls_connected", label: "Connected", color: SERIES.teal },
                  { key: "emails_sent", label: "Emails", color: SERIES.violet }, { key: "interested", label: "Interested", color: SERIES.green }]} /></div>
              <div className="card table-card" style={{ padding: 0, marginTop: 14 }}>
                <div className="table-scroll"><table data-testid="daily-table">
                  <thead><tr><th>Day</th>{DAY_COLS.map((c) => <th key={c}>{LABEL[c]}</th>)}</tr></thead>
                  <tbody>{data.days.map((d) => (
                    <tr key={d.day}><td><b>{istDay(d.day)}</b></td>{DAY_COLS.map((c) => <td key={c}>{d[c] || <span className="sub-inline">0</span>}</td>)}</tr>
                  ))}</tbody>
                </table></div>
              </div>
            </>
          )}
          {tab === "team" && (
            <>
              <div className="card"><h3 style={{ marginTop: 0 }}>Actions per person</h3>
                {data.team.length ? <HBars rows={data.team.map((p) => ({ key: p.who, label: p.who, value: p.actions, color: SERIES.blue }))} onPick={(k) => { setWho(k); setTab("log"); }} />
                  : <p className="empty">Nothing done in this period.</p>}</div>
              <div className="card table-card" style={{ padding: 0, marginTop: 14 }}>
                <div className="table-scroll"><table data-testid="team-report">
                  <thead><tr><th>Person</th><th>Actions</th>{DAY_COLS.map((c) => <th key={c}>{LABEL[c]}</th>)}</tr></thead>
                  <tbody>{data.team.map((p) => (
                    <tr key={p.who}><td><button className="link" onClick={() => { setWho(p.who); setTab("log"); }}>{p.who}</button></td><td>{p.actions}</td>
                      {DAY_COLS.map((c) => <td key={c}>{p[c]}</td>)}</tr>
                  ))}</tbody>
                </table></div>
              </div>
            </>
          )}
          {tab === "log" && (
            <div className="card table-card" style={{ padding: 0 }}>
              <div className="row" style={{ padding: "12px 16px", gap: 10, flexWrap: "wrap" }}>
                <label className="row" style={{ gap: 6, margin: 0 }}>Show
                  <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Action type" style={{ width: "auto" }}>
                    {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select></label>
                <small className="sub-inline">{data.log_total} actions{data.log_total > data.log.length ? ` (newest ${data.log.length} shown; download for all)` : ""}</small>
              </div>
              {data.log.length === 0 ? <p className="empty">Nothing in this period.</p> : (
                <div className="table-scroll"><table data-testid="activity-log">
                  <thead><tr><th>When (IST)</th><th>By</th><th>Lead</th><th>Action</th><th>Outcome</th><th>Number / email / profile used</th><th>Notes</th></tr></thead>
                  <tbody>{data.log.map((r, i) => (
                    <tr key={`${r.at}-${i}`}><td>{istDay(r.day)} {r.time_ist}</td><td>{r.who}</td>
                      <td><button className="link" onClick={() => setLead(r.lead_id)}>{r.company || "—"}</button>{r.contact && <small className="sub-inline"> · {r.contact}</small>}</td>
                      <td>{r.what}</td><td>{pretty(r.outcome)}</td>
                      <td style={{ overflowWrap: "anywhere" }}>{/^https?:\/\//i.test(r.where) ? <a href={r.where} target="_blank" rel="noopener noreferrer">{r.where.replace(/^https?:\/\/(www\.)?/, "")}</a> : r.where}</td>
                      <td><small>{r.notes}</small></td></tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          )}
        </>
      )}
      {lead && <LeadDrawer id={lead} onClose={() => setLead(null)} />}
    </>
  );
}
