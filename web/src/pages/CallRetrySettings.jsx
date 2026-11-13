import { useEffect, useState } from "react";
import { api } from "../api.js";

const KINDS = [["no_answer", "No answer"], ["busy", "Busy"], ["call_disconnected", "Call disconnected"]];
const toText = (list) => (list || []).join(", ");
const parse = (text) => String(text).split(/[\s,]+/).filter(Boolean).map(Number);

// Owner-only card on the Call desk: automatic call retries. Off until an owner turns it on.
export default function CallRetrySettings() {
  const [cfg, setCfg] = useState(null);
  const [text, setText] = useState({});
  const [max, setMax] = useState(6);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const adopt = (c) => { setCfg(c); setMax(c.max_attempts); setText(Object.fromEntries(KINDS.map(([k]) => [k, toText(c.rules[k])]))); };
  useEffect(() => { api.callRetry().then(adopt).catch((e) => setMsg(e.message)); }, []);
  if (!cfg) return msg ? <p className="error" role="alert">{msg}</p> : null;
  const save = async (enabled) => {
    setSaving(true); setMsg("");
    try {
      adopt(await api.saveCallRetry({ enabled, max_attempts: Number(max), rules: Object.fromEntries(KINDS.map(([k]) => [k, parse(text[k])])) }));
      setMsg(enabled ? "Saved. Retries are on." : "Saved. Retries are off.");
    } catch (e) { setMsg(e.message); }
    setSaving(false);
  };
  return (
    <details className="card" style={{ marginBottom: 12 }} data-testid="call-retry">
      <summary><b>Automatic call retries</b> <span className={`pill ${cfg.enabled ? "ok" : ""}`}>{cfg.enabled ? "On" : "Off"}</span></summary>
      <p className="sub">This is <b>off by default</b>. When on, a call logged as no answer, busy or call disconnected books the next try for you as a call task, so it comes back in the queue at the right time.
        The 1st unanswered try uses the 1st wait, the 2nd uses the 2nd, and the last wait repeats. A try that would land outside Monday to Saturday, 09:00 to 19:00 IST moves to 10:00 the next calling day.
        After the most tries, the lead leaves the queue and is marked <b>Retries used up</b>. A connected call (or any real answer) cancels the waiting retry.
        If you choose a call-back time yourself on a dropped call, your time is used instead.</p>
      <label>Most tries per lead (1 to 20)
        <input type="number" min="1" max="20" value={max} onChange={(e) => setMax(e.target.value)} style={{ width: 90, marginLeft: 6 }} /></label>
      {KINDS.map(([k, label]) => (
        <label key={k} style={{ display: "block", marginTop: 8 }}>{label}: waits in minutes, separated by commas (5 to 10080, up to 6)
          <input value={text[k] || ""} onChange={(e) => setText({ ...text, [k]: e.target.value })} aria-label={`${label} waits in minutes`} placeholder="180, 1440" /></label>
      ))}
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        <button className="btn" disabled={saving} onClick={() => save(true)} data-testid="retry-on">{cfg.enabled ? "Save" : "Save and turn on"}</button>
        {cfg.enabled ? <button className="btn ghost" disabled={saving} onClick={() => save(false)} data-testid="retry-off">Turn off</button>
          : <button className="btn ghost" disabled={saving} onClick={() => save(false)}>Save, keep off</button>}
      </div>
      {msg && <p className="sub" role="status">{msg}</p>}
    </details>
  );
}
