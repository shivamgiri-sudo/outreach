import { useEffect, useRef, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { stageLabel } from "../labels.js";
import { istLabel } from "../time.js";
import CallRetrySettings from "./CallRetrySettings.jsx";
import CallScript from "./CallScript.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import PhoneTag from "./PhoneTag.jsx";
import { INVALID_REASONS } from "../leadReasons.js";
import { Badge, Button, Card, DataTable, EmptyState, PageHeader, Tabs } from "../ui/index.js";

// [outcome, button label, key]. Every outcome is logged with the signed-in person's name.
const OUTCOMES = [["connected", "Connected", "1"], ["no_answer", "No answer", "2"], ["busy", "Busy", "3"], ["call_back", "Call back…", "4"],
  ["interested", "Interested", "5"], ["not_interested", "Not interested", "6"], ["wrong_number", "Wrong number", "7"],
  ["call_disconnected", "Call disconnected", "8"], ["inactive_number", "Inactive number", "9"],
  ["invalid_lead", "Invalid lead", "0"], ["consultant_lead", "Consultant lead", "c"]];
const MEANS = { interested: "Records their yes (phone), moves them to Interested and adds a follow-up for tomorrow.",
  not_interested: "Moves them to Disqualified and stops their emails.", wrong_number: "Marks this number wrong; they leave the queue until it is fixed.",
  inactive_number: "Not in service or switched off: they leave the queue until the number is changed.",
  call_disconnected: "The call dropped; counted as an unanswered attempt. Offers a call-back in 15 minutes (you can skip it).",
  call_back: "Pick a time; a call-back task is created for you.",
  invalid_lead: "Not a real or usable lead (fake, not hiring, wrong company). Disqualifies them, stops their emails and removes them from the queue.",
  consultant_lead: "A consultant or agency, not a direct employer. Marks the lead, stops their emails and removes them from the queue; their stage stays." };
const withIst = (local) => (local ? `${local}:00+05:30` : null);
const inIstMinutes = (m) => new Date(Date.now() + (330 + m) * 60000).toISOString().slice(0, 16);
const typing = (el) => el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);

function useDialLink() {
  const [dialUrl, setDialUrl] = useState("");
  useEffect(() => {
    api.integrations().then((list) => {
      const t = list.find((i) => i.id === "telephony");
      setDialUrl(t && t.mode === "api" ? t.values.click_to_call_url || "" : "");
    }).catch(() => {});
  }, []);
  // Only dialler schemes are used as a link (a saved javascript: address would run in the app when clicked).
  const usable = dialUrl.includes("{phone}") && /^(https?:\/\/|tel:|sip:|callto:)/i.test(dialUrl.trim());
  return (phone) => (usable ? dialUrl.replace("{phone}", encodeURIComponent(phone)) : `tel:${phone}`);
}

function Summary({ s }) {
  if (!s) return <p className="sub">Loading history…</p>;
  return (
    <p className="sub" data-testid="dial-history">
      {s.total_touches} touches · calls {s.calls.attempts} ({s.calls.unanswered} unanswered, {s.calls.connected} connected)
      · emails {s.emails.sent} sent, {s.emails.replies} replies{s.whatsapp.sent ? ` · WhatsApp ${s.whatsapp.sent}` : ""}
      {s.last_touch ? ` · last touch ${istLabel(s.last_touch)}` : " · never touched"}
    </p>
  );
}

// One lead at a time: context, script, dial link, outcome buttons, notes; then on to the next.
function Dialer({ queue, script, rep, onScript, onOpen, onExit }) {
  const [index, setIndex] = useState(0);
  const [notes, setNotes] = useState("");
  const [dialled, setDialled] = useState("");
  const [callback, setCallback] = useState(null);
  const [cbFor, setCbFor] = useState("call_back");
  const [reason, setReason] = useState(null);          // invalid lead: null = not asked yet, "" = asked, else the chosen reason     // which outcome the call-back time belongs to
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState([]);
  const [msg, setMsg] = useState("");
  const dialLink = useDialLink();
  const lead = queue[index];
  const state = useRef({});
  useEffect(() => {
    setNotes(""); setCallback(null); setReason(null); setSummary(null); setMsg("");
    if (lead) api.historySummary(lead.lead_id).then(setSummary).catch(() => setSummary(null));
  }, [index, lead?.lead_id]);

  const record = async (outcome, skipCallback = false) => {
    if (!lead || busy || !canWrite()) return;
    if ((outcome === "call_back" || outcome === "call_disconnected") && callback === null && !skipCallback) {
      setCbFor(outcome); setCallback(inIstMinutes(outcome === "call_back" ? 60 : 15)); return;      // a dropped call offers a retry in 15 minutes
    }
    if (outcome === "invalid_lead" && !reason) { setReason(""); setMsg(reason === "" ? "Pick why this lead is invalid first." : ""); return; }   // a reason is required
    const withCallback = callback !== null && !skipCallback && outcome === cbFor && (outcome === "call_back" || outcome === "call_disconnected");
    setBusy(true); setMsg("");
    try {
      const numbers = [lead.phone, ...(lead.phones || [])].filter(Boolean);
      const r = await api.callOutcome(lead.lead_id, outcome, notes.trim(), withCallback ? withIst(callback) : null, numbers.includes(dialled) ? dialled : lead.phone, outcome === "invalid_lead" ? reason : "");
      setDialled("");
      setLog((l) => [...l, { company: lead.company, outcome, stage: r.stage }]);
      setIndex((i) => i + 1);
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  state.current = { record, lead, callback };
  useEffect(() => {
    const onKey = (e) => {
      if (typing(e.target) || e.ctrlKey || e.metaKey || e.altKey || document.querySelector(".drawer")) return;
      const o = OUTCOMES.find(([, , k]) => k === e.key);
      if (o) { e.preventDefault(); state.current.record(o[0]); }
      if (e.key === "s") { e.preventDefault(); setIndex((i) => i + 1); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (!lead) {
    return (
      <Card data-testid="dial-done">
        <h3>Queue done</h3>
        <p className="sub">{log.length} call{log.length === 1 ? "" : "s"} logged this round{log.length ? `: ${log.map((l) => `${l.company} (${l.outcome.replace(/_/g, " ")})`).join(", ")}` : ""}.</p>
        <Button onClick={onExit}>Back to the queue</Button>
      </Card>
    );
  }
  return (
    <div className="grid g2 dialer-grid" data-testid="dialer">
      <Card className="dialer">
        <div className="dialer-top">
          <span className="eyebrow">Lead {index + 1} of {queue.length} · {lead.reason}</span>
          <div className="row">
            <Button variant="secondary" size="sm" onClick={() => setIndex((i) => i + 1)} title="Skip (S)">Skip</Button>
            <Button variant="secondary" size="sm" onClick={onExit}>Stop</Button>
          </div>
        </div>
        <h3 className="dialer-company" data-testid="dial-company">{lead.company || lead.contact_name}</h3>
        <p className="sub dialer-meta">{lead.contact_name}{lead.title ? ` · ${lead.title}` : ""}{lead.role ? ` · hiring ${lead.role}` : ""}
          <br /><span className="pill">{stageLabel(lead.stage)}</span> {lead.score != null && <span className={`pill ${lead.band || ""}`}>{lead.score} · {lead.band}</span>}
          {lead.consent && <span className="pill ok">consent</span>}</p>
        <div className="dial-actions">
          <a className="btn dial-btn" href={dialLink(lead.phone)} data-testid="dial-link" onClick={() => setDialled(lead.phone)}>Call {lead.phone}</a><PhoneTag lead={lead} />
          {(lead.phones || []).map((p) => <a key={p} className="btn ghost dial-btn" href={dialLink(p)} onClick={() => setDialled(p)} data-testid="dial-extra">Call {p}</a>)}
        </div>
        {(lead.phones || []).length > 0 && <small className="sub dial-note">Logging for {dialled || lead.phone} (the number you last tapped).</small>}
        <Summary s={summary} />
        {lead.recent_notes.length > 0 && (
          <ul className="sub dial-notes-list">
            {lead.recent_notes.map((n, i) => <li key={i}>{istLabel(n.at)} · {n.kind}{n.by ? ` · ${n.by}` : ""}: {n.text}</li>)}
          </ul>
        )}
        {canWrite() && <>
        <label htmlFor="dial-notes">Notes (saved with the outcome)</label>
        <textarea id="dial-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What they said, next step" />
        {reason !== null && (
          <label className="row dial-inline">Why is it invalid?
            <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason the lead is invalid" data-testid="invalid-reason">
              <option value="">Pick a reason…</option>
              {INVALID_REASONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></label>
        )}
        {callback !== null && (
          <label className="row dial-inline">Call back at (IST)
            <input type="datetime-local" value={callback} onChange={(e) => setCallback(e.target.value)} aria-label="Call back at" />
            {cbFor === "call_disconnected" && <Button variant="secondary" size="sm" disabled={busy} onClick={() => record("call_disconnected", true)} data-testid="drop-no-callback">No call back, just log it</Button>}</label>
        )}
        <div className="outcome-grid" role="group" aria-label="Call outcome">
          {OUTCOMES.map(([o, label, key]) => (
            <button key={o} className={`btn ${o === "interested" ? "" : "ghost"}${["not_interested", "wrong_number", "inactive_number", "invalid_lead", "consultant_lead"].includes(o) ? " danger" : ""}`} disabled={busy}
              title={`${MEANS[o] || "Log it and go to the next lead."} Key ${key}.`} onClick={() => record(o)}>
              {o === "invalid_lead" && reason !== null ? "Save as invalid" : o === cbFor && callback !== null && (o === "call_back" || o === "call_disconnected") ? "Save call back" : label}
            </button>
          ))}
        </div>
        <p className="sub">Keys 1–9 log an outcome, S skips. {callback !== null ? MEANS.call_back : "Each outcome saves and moves to the next lead."}</p>
        </>}
        {msg && <p className="error" role="alert">{msg}</p>}
        <button className="link" onClick={() => onOpen(lead.lead_id)}>Open the full lead</button>
      </Card>
      <Card><CallScript data={script} lead={lead} rep={rep} onChanged={onScript} /></Card>
    </div>
  );
}

export default function CallDesk() {
  const [data, setData] = useState(null);
  const [mine, setMine] = useState(false);
  const [open, setOpen] = useState(null);
  const [rep, setRep] = useState("your name");
  const [script, setScript] = useState(null);
  const [walking, setWalking] = useState(null);       // a snapshot of the queue while calling
  const [error, setError] = useState("");
  const dialLink = useDialLink();
  useEffect(() => { api.me().then((m) => setRep(m.name || "your name")).catch(() => {}); api.callScript().then(setScript).catch(() => {}); }, []);
  const load = () => api.callQueue(mine).then((d) => { setData(d); if (d.me) setRep(d.me); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [mine]);
  const [best, setBest] = useState(null);
  useEffect(() => { api.bestTime().then(setBest).catch(() => {}); }, []);
  const [view, setView] = useState("all");
  const everyone = data?.queue || [];
  const CHIPS = [["all", "Everyone", () => true], ["fresh", "Never called", (l) => !l.attempts],
    ["retry", "Tried before", (l) => l.attempts > 0], ["yes", "Said yes", (l) => l.rank === 3], ["back", "Call backs", (l) => l.rank <= 1]];
  const queue = everyone.filter((CHIPS.find((c) => c[0] === view) || CHIPS[0])[2]);

  return (
    <>
      <PageHeader title="Call desk" description={<>Call-backs and call tasks first, then leads who said yes, then leads with a phone and consent. <b>{script?.note}</b></>} />
      {best && <p className="sub best-time" data-testid="best-time">{best.enough && best.window
        ? `Calls reach people most between ${String(best.window.from).padStart(2, "0")}:00-${String(best.window.to).padStart(2, "0")}:00 IST (based on ${best.sample} calls).`
        : `Not enough calls yet to say when people pick up (${best.sample} of ${best.min_total} calls so far).`}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!walking && isOwner() && <CallRetrySettings />}
      {walking ? (
        <Dialer queue={walking} script={script} rep={rep} onScript={setScript} onOpen={setOpen} onExit={() => { setWalking(null); load(); }} />
      ) : (
        <div className="grid g2">
          <Card className="queue-card">
            <div className="queue-head">
              <Button icon="phone" disabled={!queue.length} onClick={() => setWalking(queue)} data-testid="start-calling">Start calling ({queue.length})</Button>
              <label className="check-row queue-mine"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
            </div>
            <Tabs label="Who to call" value={view} onChange={setView}
              items={CHIPS.map(([k, label, test]) => ({ id: k, label: `${label} (${everyone.filter(test).length})`, testId: `queue-${k}` }))} />
            {queue.length === 0 ? <EmptyState icon="phone" title={everyone.length ? "No one in this group right now." : "No one is waiting for a call right now."} /> : (
              <DataTable maxHeight="calc(100vh - 330px)" label="Call queue">
                  <thead><tr><th>Lead</th><th>Why now</th><th>Calls so far</th><th>Phone</th></tr></thead>
                  <tbody>
                    {queue.map((l, i) => (
                      <tr key={l.lead_id}>
                        <td><button className="link" onClick={() => setOpen(l.lead_id)}><b>{l.company || l.contact_name}</b></button><br />
                          <small>{l.contact_name}{l.score != null ? ` · score ${l.score}` : ""}</small>{l.retry_exhausted && <Badge tone="warning">Retries used up</Badge>}</td>
                        <td>{l.reason}{l.due_at ? <div className="sub">due {istLabel(l.due_at)}</div> : null}</td>
                        <td data-testid="queue-attempts">{l.attempts ? <>{l.attempts} call{l.attempts > 1 ? "s" : ""}<div className="sub">{l.last_outcome ? `last: ${l.last_outcome.replace(/_/g, " ")} · ` : "last "}{istLabel(l.last_attempt_at)}</div></> : <Badge tone="success">never called</Badge>}</td>
                        <td><a href={dialLink(l.phone)}>{l.phone}</a><PhoneTag lead={l} /><br /><button className="link" onClick={() => setWalking([...queue.slice(i), ...queue.slice(0, i)])}>Call from here</button></td>
                      </tr>
                    ))}
                  </tbody>
              </DataTable>
            )}
          </Card>
          <Card className="script-card"><CallScript data={script} lead={queue[0]} rep={rep} onChanged={setScript} /></Card>
        </div>
      )}
      {open && <LeadDrawer id={open} ids={(walking || queue).map((l) => l.lead_id)} onClose={() => { setOpen(null); if (!walking) load(); }} />}
    </>
  );
}
