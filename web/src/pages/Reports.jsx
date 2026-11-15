import { useEffect, useState } from "react";
import { getRole } from "../api.js";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import PeriodPicker, { usePeriod } from "../PeriodPicker.jsx";
import { HBars, SERIES, TrendChart } from "../charts.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import ResponseTimes from "./ResponseTimes.jsx";
import HelpLink from "../components/HelpLink.jsx";
import Abbr from "../components/Abbr.jsx";
import "../analytics-pages.css";
import { Button, Card, CardHeader, DataTable, EmptyState, Input, PageHeader, Select, Skeleton, Stat, Stats, Tabs } from "../ui/index.js";

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
      <PageHeader title={<>Reports <HelpLink chapter="reports" label="Reports" /></>}
        description="What was done each day and by whom. Times are India Standard Time. Every number comes from the activity log below." />
      <PeriodPicker value={period} onChange={setPeriod} range={data?.range} />
      <div className="rep-toolbar">
        <label>Person
          <Select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Person" data-testid="report-person">
            <option value="">Everyone</option>
            {(data?.people || []).map((p) => <option key={p} value={p}>{p}</option>)}
          </Select></label>
        {canCsv && <Button variant="secondary" icon="download" onClick={() => ops.dailyReportCsv(period, who, kind)} data-testid="report-csv">Download activity log (CSV)</Button>}
      </div>
      <div className="rep-toolbar">
        {canCsv && <Input icon="mail" value={mailTo} onChange={(e) => setMailTo(e.target.value)} placeholder="Also send to (emails, comma separated)" aria-label="Also send to" />}
        <Button onClick={sendMail} data-testid="report-email">Email me this report</Button>
        {mailMsg && <small className="rep-msg" role="status" data-testid="report-email-msg">{mailMsg}</small>}
      </div>
      {preview && (
        <Card as="details" className="u-mb-12" open>
          <summary><b>Email preview</b> (as sent, with the CSV attached and a download link)</summary>
          <iframe title="Report email preview" srcDoc={preview} sandbox="" className="rep-preview u-mt-12" data-testid="report-preview" />
        </Card>
      )}
      {error && <ErrorState message="The report could not load." detail={error} onRetry={load} />}
      {!data && !error && <div className="an-skel" aria-busy="true"><div className="an-skel-tiles">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} height={96} />)}</div><Skeleton height={220} /></div>}
      {data && (
        <>
          <Stats>
            {["leads_added", "calls", "calls_connected", "interested", "not_interested", "follow_ups", "emails_sent", "whatsapp_sent", "replies", "meetings"].map((k) => (
              <Stat key={k} value={t[k]} label={LABEL[k]} data-testid={`rep-${k}`} />
            ))}
          </Stats>
          <div className="an-section"><Tabs label="Report sections" value={tab} onChange={setTab}
            items={[["daily", "Daily report"], ["team", "By person"], ["log", "Activity log"], ["sla", "Response times"]].map(([id, label]) => ({ id, label }))} /></div>
          {tab === "sla" && <ResponseTimes period={period} />}
          {tab === "daily" && (
            <>
              <Card><CardHeader title="Each day" />
                <TrendChart days={[...data.days].reverse()} series={[{ key: "calls", label: "Calls", color: SERIES.blue }, { key: "calls_connected", label: "Connected", color: SERIES.teal },
                  { key: "emails_sent", label: "Emails", color: SERIES.violet }, { key: "interested", label: "Interested", color: SERIES.green }]} /></Card>
              <Card className="table-card u-p0 an-section">
                <div className="table-scroll"><DataTable label="Each day" data-testid="daily-table">
                  <thead><tr><th>Day</th>{DAY_COLS.map((c) => <th key={c}>{LABEL[c]}</th>)}</tr></thead>
                  <tbody>{data.days.map((d) => (
                    <tr key={d.day}><td><b>{istDay(d.day)}</b></td>{DAY_COLS.map((c) => <td key={c}>{d[c] || <span className="sub-inline">0</span>}</td>)}</tr>
                  ))}</tbody>
                </DataTable></div>
              </Card>
            </>
          )}
          {tab === "team" && (
            <>
              <Card><CardHeader title="Actions per person" />
                {data.team.length ? <HBars rows={data.team.map((p) => ({ key: p.who, label: p.who, value: p.actions, color: SERIES.blue }))} onPick={(k) => { setWho(k); setTab("log"); }} />
                  : <EmptyState icon="users" title="Nothing done in this period">Pick a longer period above.</EmptyState>}</Card>
              <Card className="table-card u-p0 an-section">
                <div className="table-scroll"><DataTable label="By person" data-testid="team-report">
                  <thead><tr><th>Person</th><th>Actions</th>{DAY_COLS.map((c) => <th key={c}>{LABEL[c]}</th>)}</tr></thead>
                  <tbody>{data.team.map((p) => (
                    <tr key={p.who}><td><button className="link" onClick={() => { setWho(p.who); setTab("log"); }}>{p.who}</button></td><td>{p.actions}</td>
                      {DAY_COLS.map((c) => <td key={c}>{p[c]}</td>)}</tr>
                  ))}</tbody>
                </DataTable></div>
              </Card>
            </>
          )}
          {tab === "log" && (
            <Card className="table-card u-p0">
              <div className="rep-log-head">
                <label>Show
                  <Select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Action type">
                    {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select></label>
                <small className="sub-inline">{data.log_total} actions{data.log_total > data.log.length ? ` (newest ${data.log.length} shown; download for all)` : ""}</small>
              </div>
              {data.log.length === 0 ? <EmptyState icon="history" title="Nothing in this period">Calls, emails and stage changes are listed here as they happen.</EmptyState> : (
                <div className="table-scroll"><DataTable label="Activity log" data-testid="activity-log">
                  <thead><tr><th>When (<Abbr term="IST">IST</Abbr>)</th><th>By</th><th>Lead</th><th>Action</th><th>Outcome</th><th>Number / email / profile used</th><th>Notes</th></tr></thead>
                  <tbody>{data.log.map((r, i) => (
                    <tr key={`${r.at}-${i}`}><td>{istDay(r.day)} {r.time_ist}</td><td>{r.who}</td>
                      <td><button className="link" onClick={() => setLead(r.lead_id)}>{r.company || "—"}</button>{r.contact && <small className="sub-inline"> · {r.contact}</small>}</td>
                      <td>{r.what}</td><td>{pretty(r.outcome)}</td>
                      <td className="u-anywhere">{/^https?:\/\//i.test(r.where) ? <a href={r.where} target="_blank" rel="noopener noreferrer">{r.where.replace(/^https?:\/\/(www\.)?/, "")}</a> : r.where}</td>
                      <td><small>{r.notes}</small></td></tr>
                  ))}</tbody>
                </DataTable></div>
              )}
            </Card>
          )}
        </>
      )}
      {lead && <LeadDrawer id={lead} onClose={() => setLead(null)} />}
    </>
  );
}
