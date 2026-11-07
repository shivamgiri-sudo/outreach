// Stage control for the lead drawer: move forward in one click, pick any stage the rules allow (going back needs a
// reason; an owner can force a skip with a reason), record "yes" consent and optionally move to Interested with a call task.
import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { stageLabel } from "../labels.js";
import { istLabel } from "../time.js";
import { FORWARD } from "./LeadDrawerParts.jsx";

// Consent is per channel: a yes on one channel never covers another (SMS needs its own yes too).
const CHANNELS = [["call", "Phone call"], ["whatsapp", "WhatsApp"], ["sms", "SMS"], ["email", "Email"]];
const LEGACY_ANY = ["email", "call", "whatsapp"];            // older records saved as "any channel"
export const consentChannels = (lead) => {
  const c = lead?.consent;
  const out = new Set(Object.keys(lead?.inbound_consent || {}));
  if (c) (Array.isArray(c.channels) ? c.channels : CHANNELS.some(([v]) => v === c.channel) ? [c.channel] : LEGACY_ANY).forEach((x) => out.add(x));
  return CHANNELS.map(([v]) => v).filter((v) => out.has(v));
};
const channelName = (v) => (CHANNELS.find(([c]) => c === v) || [v, v])[1];

function ConsentForm({ lead, onSaved, onCancel }) {
  const have = consentChannels(lead);
  const open = CHANNELS.filter(([v]) => !have.includes(v));
  const [text, setText] = useState("");
  const [channel, setChannel] = useState((open[0] || CHANNELS[0])[0]);
  const [move, setMove] = useState(channel === "call");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const canMove = ["new", "verified", "enriched", "contacted", "engaged"].includes(lead.stage);
  const pick = (c) => { setChannel(c); if (!touched) setMove(c === "call"); };     // phone consent: move + call task by default
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg("");
    try { onSaved(await api.consentAndMove(lead.id, text, channel, canMove && move)); } catch (err) { setMsg(err.message); }
    setBusy(false);
  };
  return (
    <form className="inline-form" onSubmit={save} aria-label="Record yes consent">
      <label>Their exact words, and where (saved as proof of consent)
        <input autoFocus required value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. “Yes, call me” — call with Amit, 3 Nov" />
      </label>
      <label>They agreed to be contacted on
        <select value={channel} onChange={(e) => pick(e.target.value)}>{(open.length ? open : CHANNELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </label>
      {canMove && (
        <label className="row" style={{ fontWeight: 400, gap: 8 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={move} onChange={(e) => { setTouched(true); setMove(e.target.checked); }} />
          Move to Interested and create a call task
        </label>
      )}
      {msg && <div className="tip error" role="alert">{msg}</div>}
      <div className="row">
        <button className="btn" type="submit" disabled={busy || text.trim().length < 3}>{busy ? "Saving…" : "Save consent"}</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function LeadStage({ lead, onLead, onError }) {
  const [options, setOptions] = useState([]);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [form, setForm] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setTarget(""); setReason("");
    api.stageOptions(lead.id).then((r) => setOptions(r.options)).catch(() => setOptions([]));
  }, [lead.id, lead.stage, !!lead.consent]);
  const chosen = options.find((o) => o.stage === target);
  const needsReason = chosen && chosen.rule !== "yes";
  const next = FORWARD[lead.stage];
  const move = async (stage, why = "", force = false) => {
    setBusy(true); onError("");
    try { onLead(await api.moveStageWith(lead.id, stage, why, force)); setForm(""); setTarget(""); setReason(""); }
    catch (e) { onError(e.message); }
    setBusy(false);
  };
  const nextOk = next && options.find((o) => o.stage === next)?.rule === "yes";
  return (
    <section aria-label="Stage">
      <h3>Stage: {stageLabel(lead.stage)}</h3>
      {canWrite() && <div className="row" style={{ flexWrap: "wrap" }}>
        {nextOk && <button className="btn" disabled={busy} onClick={() => move(next)}>Move to {stageLabel(next)}</button>}
        {consentChannels(lead).length < CHANNELS.length && <button className="btn ghost" onClick={() => setForm("consent")}>
          {lead.consent ? "Record consent for another channel" : "Record “yes” consent"}</button>}
        <select aria-label="Move to stage" value={target} onChange={(e) => setTarget(e.target.value)} style={{ minWidth: 180 }}>
          <option value="">Move to any stage…</option>
          {options.filter((o) => o.stage !== "unsubscribed").map((o) => (
            <option key={o.stage} value={o.stage} disabled={!o.allowed}>
              {stageLabel(o.stage)}{o.rule === "reason" ? " (going back)" : o.rule === "owner" ? " (owner: skip steps)" : ""}{!o.allowed ? " — not allowed" : ""}
            </option>
          ))}
        </select>
        {lead.stage !== "unsubscribed" && <button className="btn ghost danger" onClick={() => setForm("unsubscribe")}>Unsubscribed</button>}
      </div>}
      {chosen && canWrite() && (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); move(chosen.stage, reason.trim(), chosen.rule === "owner"); }}>
          {chosen.why && <p className="sub" style={{ margin: 0 }}>{chosen.why}</p>}
          {needsReason && <label>Why? (saved on the lead with your name)
            <input autoFocus required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. moved by mistake / they called back" /></label>}
          <div className="row">
            <button className="btn" type="submit" disabled={busy || (needsReason && !reason.trim())}>Move to {stageLabel(chosen.stage)}</button>
            <button className="btn ghost" type="button" onClick={() => setTarget("")}>Cancel</button>
          </div>
        </form>
      )}
      {form === "consent" && canWrite() && <ConsentForm lead={lead} onCancel={() => setForm("")} onSaved={(l) => { onLead(l); setForm(""); }} />}
      {form === "unsubscribe" && canWrite() && (
        <div className="tip error" role="alert">
          This stops every email and WhatsApp to them for good and cannot be undone here.
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn danger-solid" onClick={() => move("unsubscribed")}>Yes, unsubscribe them</button>
            <button className="btn ghost" onClick={() => setForm("")}>Cancel</button>
          </div>
        </div>
      )}
      {lead.consent && <p className="sub" data-testid="consent-saved">Consent saved: “{lead.consent.text}” on {istLabel(lead.consent.at)}.
        {" "}Covers: {consentChannels(lead).map(channelName).join(", ") || "no channel yet"} (each channel needs its own yes).</p>}
      {lead.phone_status?.status === "wrong_number" && lead.phone_status.number === lead.phone && <p className="sub"><span className="pill warn">Wrong number</span> {lead.phone_status.number} marked by {lead.phone_status.by} — fix it with Edit details.</p>}
    </section>
  );
}
