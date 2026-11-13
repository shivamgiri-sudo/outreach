import { useEffect, useRef, useState } from "react";
import { api, canWrite } from "../api.js";
import { stageLabel } from "../labels.js";
import { istLabel } from "../time.js";
import CallScript from "./CallScript.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import PhoneTag from "./PhoneTag.jsx";

// [outcome, button label, key]. Every outcome is logged with the signed-in person's name.
const OUTCOMES = [["connected", "Connected", "1"], ["no_answer", "No answer", "2"], ["busy", "Busy", "3"], ["call_back", "Call back…", "4"],
  ["interested", "Interested", "5"], ["not_interested", "Not interested", "6"], ["wrong_number", "Wrong number", "7"],
  ["call_disconnected", "Call disconnected", "8"], ["inactive_number", "Inactive number", "9"],
  ["invalid_lead", "Invalid lead", "0"], ["consultant_lead", "Consultant lead", "c"]];
const MEANS = { interested: "Records their yes (phone), moves them to Interested and adds a follow-up for tomorrow.",
  not_interested: "Moves them to Disqualified and stops their emails.", wrong_number: "Marks this number wrong; they leave the queue until it is fixed.",
  inactive_number: "Not in service or switched off: they leave the queue until the number is changed.",
  call_disconnected: "The call dropped; counted as an unanswered attempt.",
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
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState([]);
  const [msg, setMsg] = useState("");
  const dialLink = useDialLink();
  const lead = queue[index];
  const state = useRef({});
  useEffect(() => {
    setNotes(""); setCallback(null); setSummary(null); setMsg("");
    if (lead) api.historySummary(lead.lead_id).then(setSummary).catch(() => setSummary(null));
  }, [index, lead?.lead_id]);

  const record = async (outcome) => {
    if (!lead || busy || !canWrite()) return;
    if (outcome === "call_back" && callback === null) { setCallback(inIstMinutes(60)); return; }
    setBusy(true); setMsg("");
    try {
      const numbers = [lead.phone, ...(lead.phones || [])].filter(Boolean);
      const r = await api.callOutcome(lead.lead_id, outcome, notes.trim(), outcome === "call_back" ? withIst(callback) : null, numbers.includes(dialled) ? dialled : lead.phone);
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
      <div className="card" data-testid="dial-done">
        <h3 style={{ marginTop: 0 }}>Queue done</h3>
        <p className="sub">{log.length} call{log.length === 1 ? "" : "s"} logged this round{log.length ? `: ${log.map((l) => `${l.company} (${l.outcome.replace(/_/g, " ")})`).join(", ")}` : ""}.</p>
        <button className="btn" onClick={onExit}>Back to the queue</button>
      </div>
    );
  }
  return (
    <div className="grid g2" data-testid="dialer">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <small className="sub" style={{ margin: 0 }}>Lead {index + 1} of {queue.length} · {lead.reason}</small>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn ghost" onClick={() => setIndex((i) => i + 1)} title="Skip (S)">Skip</button>
            <button className="btn ghost" onClick={onExit}>Stop</button>
          </div>
        </div>
        <h3 style={{ margin: "8px 0 2px" }} data-testid="dial-company">{lead.company || lead.contact_name}</h3>
        <p className="sub" style={{ margin: 0 }}>{lead.contact_name}{lead.title ? ` · ${lead.title}` : ""}{lead.role ? ` · hiring ${lead.role}` : ""}
          <br /><span className="pill">{stageLabel(lead.stage)}</span> {lead.score != null && <span className={`pill ${lead.band || ""}`}>{lead.score} · {lead.band}</span>}
          {lead.consent && <span className="pill ok" style={{ marginLeft: 4 }}>consent</span>}</p>
        <a className="btn dial-btn" href={dialLink(lead.phone)} style={{ display: "inline-block", margin: "10px 0", fontSize: 18 }} data-testid="dial-link" onClick={() => setDialled(lead.phone)}>Call {lead.phone}</a><PhoneTag lead={lead} />
        {(lead.phones || []).map((p) => <a key={p} className="btn ghost" href={dialLink(p)} onClick={() => setDialled(p)} style={{ marginLeft: 6 }} data-testid="dial-extra">Call {p}</a>)}
        {(lead.phones || []).length > 0 && <small className="sub" style={{ display: "block" }}>Logging for {dialled || lead.phone} (the number you last tapped).</small>}
        <Summary s={summary} />
        {lead.recent_notes.length > 0 && (
          <ul className="sub" style={{ paddingLeft: 18, margin: "4px 0" }}>
            {lead.recent_notes.map((n, i) => <li key={i}>{istLabel(n.at)} · {n.kind}{n.by ? ` · ${n.by}` : ""}: {n.text}</li>)}
          </ul>
        )}
        {canWrite() && <>
        <label htmlFor="dial-notes">Notes (saved with the outcome)</label>
        <textarea id="dial-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What they said, next step" />
        {callback !== null && (
          <label className="row" style={{ gap: 6, marginTop: 6 }}>Call back at (IST)
            <input type="datetime-local" value={callback} onChange={(e) => setCallback(e.target.value)} aria-label="Call back at" /></label>
        )}
        <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }} aria-label="Call outcome">
          {OUTCOMES.map(([o, label, key]) => (
            <button key={o} className={`btn ${o === "interested" ? "" : "ghost"}${["not_interested", "wrong_number", "inactive_number", "invalid_lead", "consultant_lead"].includes(o) ? " danger" : ""}`} disabled={busy}
              title={`${MEANS[o] || "Log it and go to the next lead."} Key ${key}.`} onClick={() => record(o)}>
              {o === "call_back" && callback !== null ? "Save call back" : label}
            </button>
          ))}
        </div>
        <p className="sub">Keys 1–9 log an outcome, S skips. {callback !== null ? MEANS.call_back : "Each outcome saves and moves to the next lead."}</p>
        </>}
        {msg && <p className="error" role="alert">{msg}</p>}
        <button className="link" onClick={() => onOpen(lead.lead_id)}>Open the full lead</button>
      </div>
      <div className="card"><CallScript data={script} lead={lead} rep={rep} onChanged={onScript} /></div>
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
  const [view, setView] = useState("all");
  const everyone = data?.queue || [];
  const CHIPS = [["all", "Everyone", () => true], ["fresh", "Never called", (l) => !l.attempts],
    ["retry", "Tried before", (l) => l.attempts > 0], ["yes", "Said yes", (l) => l.rank === 3], ["back", "Call backs", (l) => l.rank <= 1]];
  const queue = everyone.filter((CHIPS.find((c) => c[0] === view) || CHIPS[0])[2]);

  return (
    <>
      <h2>Call desk</h2>
      <p className="sub">Call-backs and call tasks first, then leads who said yes, then leads with a phone and consent. <b>{script?.note}</b></p>
      {error && <p className="error" role="alert">{error}</p>}
      {walking ? (
        <Dialer queue={walking} script={script} rep={rep} onScript={setScript} onOpen={setOpen} onExit={() => { setWalking(null); load(); }} />
      ) : (
        <div className="grid g2">
          <div className="card" style={{ padding: 0 }}>
            <div className="row" style={{ padding: 12, justifyContent: "space-between", flexWrap: "wrap" }}>
              <button className="btn" disabled={!queue.length} onClick={() => setWalking(queue)} data-testid="start-calling">Start calling ({queue.length})</button>
              <label className="row" style={{ gap: 6, fontWeight: 400 }}><input type="checkbox" style={{ width: "auto" }} checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine</label>
            </div>
            <div className="row" style={{ padding: "0 12px 8px", gap: 6, flexWrap: "wrap" }} role="group" aria-label="Who to call">
              {CHIPS.map(([k, label, test]) => (
                <button key={k} type="button" className={`btn ghost${view === k ? " on" : ""}`} aria-pressed={view === k} onClick={() => setView(k)} data-testid={`queue-${k}`}>
                  {label} ({everyone.filter(test).length})
                </button>
              ))}
            </div>
            {queue.length === 0 ? <p className="empty">{everyone.length ? "No one in this group right now." : "No one is waiting for a call right now."}</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Lead</th><th>Why now</th><th>Calls so far</th><th>Phone</th></tr></thead>
                  <tbody>
                    {queue.map((l, i) => (
                      <tr key={l.lead_id}>
                        <td><button className="link" onClick={() => setOpen(l.lead_id)}><b>{l.company || l.contact_name}</b></button><br />
                          <small>{l.contact_name}{l.score != null ? ` · score ${l.score}` : ""}</small></td>
                        <td>{l.reason}{l.due_at ? <div className="sub">due {istLabel(l.due_at)}</div> : null}</td>
                        <td data-testid="queue-attempts">{l.attempts ? <>{l.attempts} call{l.attempts > 1 ? "s" : ""}<div className="sub">{l.last_outcome ? `last: ${l.last_outcome.replace(/_/g, " ")} · ` : "last "}{istLabel(l.last_attempt_at)}</div></> : <span className="pill ok">never called</span>}</td>
                        <td><a href={dialLink(l.phone)}>{l.phone}</a><PhoneTag lead={l} /><br /><button className="link" onClick={() => setWalking([...queue.slice(i), ...queue.slice(0, i)])}>Call from here</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div className="card"><CallScript data={script} lead={queue[0]} rep={rep} onChanged={setScript} /></div>
        </div>
      )}
      {open && <LeadDrawer id={open} ids={(walking || queue).map((l) => l.lead_id)} onClose={() => { setOpen(null); if (!walking) load(); }} />}
    </>
  );
}
