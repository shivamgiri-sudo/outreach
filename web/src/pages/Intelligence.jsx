import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";

const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");
const GRID = { display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" };
const role = (k) => ({ chro: "CHRO", ceo_founder: "CEO / founder", ta_head: "TA head", hr_head: "HR head" }[k] || String(k).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()));
const Stat = ({ n, label, note }) => <div className="card"><b style={{ fontSize: 26 }}>{n ?? "—"}</b><div className="sub">{label}</div>{note && <small className="sub-inline">{note}</small>}</div>;

function Bars({ rows, label, value }) {
  const max = Math.max(1, ...rows.map((r) => r[value]));
  return rows.map((r) => (
    <div className="funnel-row" key={r[label]}>
      <span>{r[label]}</span>
      <div className="bar" role="img" aria-label={`${r[label]}: ${r[value]}`} style={{ width: `${(r[value] / max) * 100}%` }} />
      <small>{r[value]}</small>
    </div>
  ));
}

export default function Intelligence() {
  const [d, setD] = useState(null);
  const [log, setLog] = useState([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const remind = () => api.remindStale().then((r) => { setNote(`${r.created} follow-up task${r.created === 1 ? "" : "s"} created (${r.stale - r.created} already had one).`); load(); }).catch((e) => setNote(e.message));
  const exportCsv = () => api.downloadChangeLog().catch((e) => setNote(e.message));
  const load = () => { setError(""); api.intelligence().then(setD).catch((e) => setError(e.message)); api.changeLog(50).then(setLog).catch(() => {}); };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message="Intelligence could not load." detail={error} onRetry={load} />;
  if (!d) return <p>Loading…</p>;
  const { channels: ch, timing: t, quality: q } = d;
  return (
    <>
      <h2>Intelligence</h2>
      <p className="sub">What is working, who replies, when, and where the data needs care. Everything here is worked out from what the team has already done.</p>

      <h3>Email</h3>
      <div style={GRID}>
        <Stat n={ch.email.sent} label="leads emailed" /><Stat n={ch.email.opened} label="opened" /><Stat n={ch.email.replied} label="replied" note={`${ch.email.reply_rate}% of those emailed`} />
      </div>
      <h3>LinkedIn</h3>
      <div style={GRID}>
        <Stat n={ch.linkedin.started} label="started" /><Stat n={ch.linkedin.connect_sent} label="requests sent" />
        <Stat n={ch.linkedin.accepted} label="accepted" note={`${ch.linkedin.accept_rate}% accept`} /><Stat n={ch.linkedin.replied} label="replied" note={`${ch.linkedin.reply_rate}% of accepted`} /><Stat n={ch.linkedin.open_profiles} label="Open Profiles" note="can be messaged without connecting" />
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>When people reply</h3>
          <p className="sub">{t.replies_measured ? <>Best day <b>{t.best_day}</b>, best hour <b>{t.best_hour_ist} IST</b>{t.median_hours_to_reply != null && <>, usually after <b>{t.median_hours_to_reply} h</b></>}.</> : "No replies yet to measure."}</p>
          <Bars rows={t.by_weekday} label="day" value="replies" />
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Which roles respond</h3>
          {d.personas.length === 0 ? <p className="empty">No leads yet.</p> : (
            <table><thead><tr><th>Role</th><th>Leads</th><th>Contacted</th><th>Replied</th><th>Interested</th></tr></thead>
              <tbody>{d.personas.map((p) => <tr key={p.persona}><td>{role(p.persona)}</td><td>{p.leads}</td><td>{p.contacted}</td><td>{p.replied} <small>({p.reply_rate}%)</small></td><td>{p.interested}</td></tr>)}</tbody></table>
          )}
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <b>Connection note test</b> <small className="sub-inline">each new lead gets note A or B, shared evenly</small>
        <table><thead><tr><th>Note</th><th>Requests sent</th><th>Accepted</th><th>Accept rate</th></tr></thead>
          <tbody>{["A", "B"].map((v) => { const x = ch.linkedin.variants?.[v] || {}; return <tr key={v}><td>{v}</td><td>{x.sent ?? 0}</td><td>{x.accepted ?? 0}</td><td>{x.accept_rate ?? 0}%</td></tr>; })}</tbody></table>
        <small className="sub-inline">Wait for 30+ requests per note before trusting the gap.</small>
      </div>
      {note && <p className="sub" role="status" style={{ marginTop: 10 }}>{note}</p>}

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Companies that engage</h3>
          {d.companies.length === 0 ? <p className="empty">Nobody has been contacted yet.</p> : (
            <table><thead><tr><th>Company</th><th>Leads</th><th>Replied</th><th>Interested</th><th>Last touch</th></tr></thead>
              <tbody>{d.companies.map((c) => <tr key={c.company}><td><b>{c.company}</b></td><td>{c.leads}</td><td>{c.replied}</td><td>{c.interested}</td><td><small>{when(c.last_touch)}</small></td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Going cold ({d.stale.count})</h3>
          <p className="sub">Interested leads with no activity for {d.stale.days} days. Pick them up before they forget you.</p>
          {d.stale.leads.map((l) => <div key={l.id}><b>{l.company}</b> <small className="sub-inline">{l.stage} · last {when(l.last_touch)}</small></div>)}
          {d.stale.count === 0 ? <p className="empty">Nothing is going cold.</p> : <button className="btn" onClick={remind} style={{ marginTop: 8 }}>Create follow-up tasks for all {d.stale.count}</button>}
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Team activity (30 days)</h3>
          {d.team.length === 0 ? <p className="empty">No actions with a name on them yet.</p> : d.team.map((m) => (
            <div key={m.who}><b>{m.who}</b> <small className="sub-inline">{m.total} action{m.total === 1 ? "" : "s"} · {Object.entries(m.by_kind).map(([k, v]) => `${v} ${k}`).join(", ")}</small></div>
          ))}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Data quality</h3>
          <p className="sub">{q.leads} leads: {q.no_email} without email, {q.no_phone} without phone, {q.no_linkedin} without a LinkedIn link, {q.role_emails} generic (info@…) addresses, {q.invalid_email} failed a mailbox check.</p>
          <p className="sub">{q.manual_corrections} manual corrections{q.corrections_by_person.length ? ": " + q.corrections_by_person.map((p) => `${p.who} ${p.count}`).join(", ") : ""}.</p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><h3 style={{ marginTop: 0 }}>Change log</h3><button className="btn" onClick={exportCsv}>Download CSV</button></div>
        <p className="sub">Every manual correction to a lead: who, when, what it was and what it became.</p>
        {log.length === 0 ? <p className="empty">No corrections yet.</p> : (
          <div className="table-wrap"><table><thead><tr><th>When</th><th>Who</th><th>Lead</th><th>Field</th><th>Was</th><th>Now</th><th>Why</th></tr></thead>
            <tbody>{log.map((r, i) => <tr key={i}><td><small>{when(r.at)}</small></td><td>{r.by}</td><td>{r.company}</td><td>{r.label}</td><td>{r.old || "—"}</td><td>{r.new || "—"}</td><td><small>{r.reason}</small></td></tr>)}</tbody></table></div>
        )}
      </div>
    </>
  );
}
