import { useEffect, useState } from "react";
import { istLabel } from "../time.js";
import { api, canWrite, isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import "../analytics-pages.css";
import { Button, Card, CardHeader, DataTable, EmptyState, PageHeader, Skeleton, Stat, Stats } from "../ui/index.js";

const when = (iso) => (iso ? istLabel(iso) : "—");
const role = (k) => ({ chro: "CHRO", ceo_founder: "CEO / founder", ta_head: "TA head", hr_head: "HR head" }[k] || String(k).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()));
const Tile = ({ n, label, note }) => <Stat value={n ?? "—"} label={label} hint={note} />;

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

export default function Intelligence({ range, embedded = false }) {
  const [d, setD] = useState(null);
  const [log, setLog] = useState([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const remind = () => api.remindStale().then((r) => { setNote(`${r.created} follow-up task${r.created === 1 ? "" : "s"} created (${r.stale - r.created} already had one).`); load(); }).catch((e) => setNote(e.message));
  const exportCsv = () => api.downloadChangeLog().catch((e) => setNote(e.message));
  const load = () => { setError(""); (range ? ops.intelligence(range) : api.intelligence()).then(setD).catch((e) => setError(e.message)); api.changeLog(50).then(setLog).catch(() => {}); };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message="Intelligence could not load." detail={error} onRetry={load} />;
  if (!d) return <div className="an-skel" aria-busy="true"><div className="an-skel-tiles">{[0, 1, 2].map((i) => <Skeleton key={i} height={96} />)}</div><Skeleton height={220} /></div>;
  const { channels: ch, timing: t, quality: q } = d;
  return (
    <>
      {(() => { const text = `${range ? "For leads created or active in the chosen period. " : ""}What is working, who replies, when, and where the data needs care. Everything here is worked out from what the team has already done.`;
        return embedded ? <p className="sub">{text}</p> : <PageHeader title="Intelligence" description={text} />; })()}

      <h3>Email</h3>
      <Stats>
        <Tile n={ch.email.sent} label="leads emailed" /><Tile n={ch.email.opened} label="opened" /><Tile n={ch.email.replied} label="replied" note={`${ch.email.reply_rate}% of those emailed`} />
      </Stats>
      <h3 className="an-section">LinkedIn</h3>
      <Stats>
        <Tile n={ch.linkedin.started} label="started" /><Tile n={ch.linkedin.connect_sent} label="requests sent" />
        <Tile n={ch.linkedin.accepted} label="accepted" note={`${ch.linkedin.accept_rate}% accept`} /><Tile n={ch.linkedin.replied} label="replied" note={`${ch.linkedin.reply_rate}% of accepted`} /><Tile n={ch.linkedin.open_profiles} label="Open Profiles" note="can be messaged without connecting" />
      </Stats>

      <div className="an-grid">
        <Card>
          <CardHeader title="When people reply" />
          <p className="sub">{t.replies_measured ? <>Best day <b>{t.best_day}</b>, best hour <b>{t.best_hour_ist} IST</b>{t.median_hours_to_reply != null && <>, usually after <b>{t.median_hours_to_reply} h</b></>}.</> : "No replies yet to measure."}</p>
          <Bars rows={t.by_weekday} label="day" value="replies" />
        </Card>
        <Card className="an-card-table">
          <h3>Which roles respond</h3>
          {d.personas.length === 0 ? <EmptyState icon="users" title="No leads yet" /> : (
            <DataTable label="Which roles respond"><thead><tr><th>Role</th><th>Leads</th><th>Contacted</th><th>Replied</th><th>Interested</th></tr></thead>
              <tbody>{d.personas.map((p) => <tr key={p.persona}><td>{role(p.persona)}</td><td>{p.leads}</td><td>{p.contacted}</td><td>{p.replied}{p.contacted > 0 && <small> ({p.reply_rate}%)</small>}</td><td>{p.interested}</td></tr>)}</tbody></DataTable>
          )}
        </Card>
      </div>
      <Card className="an-card-table an-section">
        <h3>Connection note test</h3>
        <p className="sub">Each new lead gets note A or B, shared evenly.</p>
        <DataTable label="Connection note test"><thead><tr><th>Note</th><th>Requests sent</th><th>Accepted</th><th>Accept rate</th></tr></thead>
          <tbody>{["A", "B"].map((v) => { const x = ch.linkedin.variants?.[v] || {}; return <tr key={v}><td>{v}</td><td>{x.sent ?? 0}</td><td>{x.accepted ?? 0}</td><td>{x.accept_rate ?? 0}%</td></tr>; })}</tbody></DataTable>
        <p className="an-notes u-p-16">Wait for 30+ requests per note before trusting the gap.</p>
      </Card>
      {note && <p className="an-notes" role="status">{note}</p>}

      <div className="an-grid">
        <Card className="an-card-table">
          <h3>Companies that engage</h3>
          {d.companies.length === 0 ? <EmptyState icon="building" title="Nobody has been contacted yet">Companies show here once they reply or are emailed.</EmptyState> : (
            <DataTable label="Companies that engage"><thead><tr><th>Company</th><th>Leads</th><th>Replied</th><th>Interested</th><th>Last touch</th></tr></thead>
              <tbody>{d.companies.map((c) => <tr key={c.company}><td><b>{c.company}</b></td><td>{c.leads}</td><td>{c.replied}</td><td>{c.interested}</td><td><small>{when(c.last_touch)}</small></td></tr>)}</tbody></DataTable>
          )}
        </Card>
        <Card>
          <CardHeader title={`Going cold (${d.stale.count})`} description={`Interested leads with no activity for ${d.stale.days} days. Pick them up before they forget you.`} />
          <ul className="an-list">{d.stale.leads.map((l) => <li key={l.id}><b>{l.company}</b> <small className="sub-inline">{l.stage} · last {when(l.last_touch)}</small></li>)}</ul>
          {d.stale.count === 0 ? <EmptyState icon="check" title="Nothing is going cold">Every interested lead has been touched recently.</EmptyState> : canWrite() && <Button className="u-mt-12" onClick={remind}>Create follow-up tasks for all {d.stale.count}</Button>}
        </Card>
      </div>

      <div className="an-grid">
        <Card>
          <CardHeader title={`Team activity (${range ? "this period" : "30 days"})`} />
          {d.team.length === 0 ? <EmptyState icon="users" title="No actions with a name on them yet" /> : <ul className="an-list">{d.team.map((m) => (
            <li key={m.who}><b>{m.who}</b> <small className="sub-inline">{m.total} action{m.total === 1 ? "" : "s"} · {Object.entries(m.by_kind).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}</small></li>
          ))}</ul>}
        </Card>
        <Card>
          <CardHeader title="Data quality" />
          <p className="sub">{q.leads} leads: {q.no_email} without email, {q.no_phone} without phone, {q.no_linkedin} without a LinkedIn link, {q.role_emails} generic (info@…) addresses, {q.invalid_email} failed a mailbox check.</p>
          <p className="sub">{q.manual_corrections} manual corrections{q.corrections_by_person.length ? ": " + q.corrections_by_person.map((p) => `${p.who} ${p.count}`).join(", ") : ""}.</p>
        </Card>
      </div>

      <Card className="an-section">
        <CardHeader title="Change log" description="Every manual correction to a lead: who, when, what it was and what it became." actions={isOwner() && <Button onClick={exportCsv}>Download CSV</Button>} />
        {log.length === 0 ? <EmptyState icon="history" title="No corrections yet">Edits people make to a lead's details are listed here.</EmptyState> : (
          <div className="table-wrap"><DataTable label="Change log"><thead><tr><th>When</th><th>Who</th><th>Lead</th><th>Field</th><th>Was</th><th>Now</th><th>Why</th></tr></thead>
            <tbody>{log.map((r, i) => <tr key={i}><td><small>{when(r.at)}</small></td><td>{r.by}</td><td>{r.company}</td><td>{r.label}</td><td>{r.old || "—"}</td><td>{r.new || "—"}</td><td><small>{r.reason}</small></td></tr>)}</tbody></DataTable></div>
        )}
      </Card>
    </>
  );
}
