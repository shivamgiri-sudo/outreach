// Pieces of the lead drawer (moved out of LeadDrawer.jsx so the drawer itself stays a short list of tabs).
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { SEQUENCE_STOPPED, sourceLabel, useSourceRegistry } from "../labels.js";
import { istLabel } from "../time.js";
import { getBrand } from "../brand.js";

export const SIGNAL_LABELS = {
  hiring_volume: ["Hiring volume", 30], hiring_pain: ["Hiring pain", 25], fit: ["Fit", 15],
  engagement: ["Engagement", 20], intent: ["Intent", 10],
};
export const FORWARD = {
  new: "verified", verified: "enriched", enriched: "contacted", contacted: "engaged",
  engaged: "interested", interested: "whatsapp_active", whatsapp_active: "call_done",
  call_done: "trial", trial: "customer", customer: "advocate",
};

const REPLY_MEANING = {
  interested: "consent recorded and moved to Interested.",
  not_interested: "they said no. Moved to Disqualified; no more emails.",
  unsubscribe: "unsubscribed and added to the do-not-contact list.",
  not_now: "paused (Not now); no more emails for now.",
  bounce: "the address bounced. Added to the do-not-contact list.",
  auto_reply: "an automatic reply (out of office). Logged only; the sequence keeps going.",
  wrong_person: "a task was created to find the right contact.",
  question: "a task was created to answer it.",
  other: "a task was created for you to read it.",
};

export function replyMeaning(label, effect) {
  if (effect === "review") return "the lead is past this stage, so nothing was changed automatically. A task was created for you to check.";
  return REPLY_MEANING[label] || "logged.";
}

// Short, honest drafts for a person to send on LinkedIn (connection notes are limited to 300 characters).
export function linkedinDrafts(lead) {
  const first = (lead.contact_name || "").replace(/^(mr|mrs|ms|dr)\.?\s+/i, "").split(" ")[0];
  const hi = first ? `Hi ${first}` : "Hi";
  const company = (lead.company || lead.domain || "your team").replace(/\s*\(.*?\)/g, "").replace(/\s+(pvt|private|ltd|limited|llp)\.?/gi, "").trim();
  const role = lead.role ? lead.role.toLowerCase() : "";
  const hiring = role ? `hiring a ${role}` : "hiring";
  const { public: brand, full } = getBrand();
  if (!brand.is_default) {
    // A rebranded install: honest, neutral notes naming the owner's own company and first value point.
    const value = full?.offer?.value_1 ? ` We help with: ${full.offer.value_1}.` : "";
    return [
      ["Connection note", `${hi}, I came across ${company} and thought it would be good to connect. I work at ${brand.company_name}. Happy to connect.`.slice(0, 300)],
      ["First message (after they accept)", `${hi}, thanks for connecting. I'm with ${brand.product_name}.${value} Would a short chat be useful? No pressure either way.`],
      ["Comment on their post", `Useful post, thanks for sharing. Curious how ${company} approaches this.`],
    ];
  }
  return [
    ["Connection note", `${hi}, I saw ${company} is ${hiring}. I work on BookMyInterview, which screens applicants once so you only interview the right few. Happy to connect.`.slice(0, 300)],
    ["First message (after they accept)", `${hi}, thanks for connecting. If ${role || "your open role"} is bringing more CVs than you can read, I can draft a free sample screen for it. Want me to send it? No pressure either way.`],
    ["Comment on their hiring post", `Lots of CVs and only a few right fits is the usual story for roles like this. Three short role-specific questions before any call help a lot. Happy to share a sample if useful.`],
  ];
}

export function sequenceStatus(seq) {
  if (seq.stopped) return `Stopped (${seq.stopped}) — ${SEQUENCE_STOPPED[seq.stopped] || "no more emails"}`;
  if (seq.in_flight) return "Needs a check: the app stopped mid-send. Look in the outbox before anything else is sent.";
  if (seq.blocked) return `Waiting: ${seq.blocked}.`;
  return `Running — steps sent on day(s): ${seq.sent_days.join(", ") || "none yet"}`;
}

// A small inline form instead of a browser pop-up: labelled fields, Save and Cancel.
export function InlineForm({ fields, submitLabel, onSubmit, onCancel }) {
  const [values, setValues] = useState(Object.fromEntries(fields.map((f) => [f.key, f.initial || ""])));
  const first = useRef(null);
  useEffect(() => { first.current?.focus(); }, []);
  const ready = fields.every((f) => !f.required || String(values[f.key]).trim());
  return (
    <form className="inline-form" onSubmit={(e) => { e.preventDefault(); if (ready) onSubmit(values); }}>
      {fields.map((f, i) => (
        <label key={f.key}>
          {f.label}
          {f.options ? (
            <select ref={i === 0 ? first : undefined} value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
              {f.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          ) : (
            <input ref={i === 0 ? first : undefined} type={f.type || "text"} value={values[f.key]} required={f.required}
              placeholder={f.placeholder || ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          )}
        </label>
      ))}
      <div className="row">
        <button className="btn" type="submit" disabled={!ready}>{submitLabel}</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// Fix a wrong email, number or name by hand. Every change is logged with who made it and shows in the history below.
const EDIT_FIELDS = [["contact_name", "Contact name"], ["title", "Title"], ["email", "Email"], ["phone", "Phone"], ["company", "Company"],
  ["website", "Website"], ["linkedin_url", "LinkedIn profile link"]];

export function EditDetails({ lead, onSaved }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState({});
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const start = () => { setValues(Object.fromEntries(EDIT_FIELDS.map(([k]) => [k, lead[k] || ""]))); setReason(""); setMsg(""); setOpen(true); };
  const changed = Object.fromEntries(EDIT_FIELDS.filter(([k]) => (values[k] || "") !== (lead[k] || "")).map(([k]) => [k, values[k] || ""]));
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg("");
    try { const r = await api.editLead(lead.id, changed, reason); setOpen(false); onSaved(r); } catch (err) { setMsg(err.message); }
    setBusy(false);
  };
  if (!open) return <button className="btn ghost" onClick={start}>Edit details</button>;
  return (
    <form className="inline-form" onSubmit={save} aria-label="Edit contact details">
      {EDIT_FIELDS.map(([k, label]) => (
        <label key={k}>{label}
          <input value={values[k] || ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
        </label>
      ))}
      <label>Why are you changing it? (optional)
        <input value={reason} placeholder="e.g. wrong number on the card" onChange={(e) => setReason(e.target.value)} />
      </label>
      {msg && <div className="tip error" role="alert">{msg}</div>}
      <p className="sub" style={{ margin: 0 }}>Saved with your name, the time, the old and the new value.</p>
      <div className="row">
        <button className="btn" type="submit" disabled={busy || !Object.keys(changed).length}>{busy ? "Saving…" : `Save ${Object.keys(changed).length || ""} change${Object.keys(changed).length === 1 ? "" : "s"}`}</button>
        <button className="btn ghost" type="button" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

// DPDP (owner only): download everything held on this person, or erase it for good.
export function PrivacyActions({ lead, onErased }) {
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");
  const exportData = async () => {
    try {
      const blob = await api.download(`/api/leads/${lead.id}/export`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `lead-${lead.id}.json`; a.click();
    } catch (e) { setMessage(e.message); }
  };
  const erase = async () => {
    try { await api.eraseLead(lead.id); onErased(); } catch (e) { setMessage(e.message); }
  };
  return (
    <section data-testid="privacy-actions">
      <h3>Their data (DPDP)</h3>
      <div className="row">
        <button className="btn ghost" onClick={exportData}>Download their data</button>
        {!confirming && <button className="btn ghost danger" onClick={() => setConfirming(true)}>Erase their data…</button>}
      </div>
      {confirming && (
        <div className="tip error" role="alert">
          This deletes the lead, its emails, replies, messages, tasks and notes for good. Only a scrambled fingerprint of
          the email and phone is kept, so they are never imported or contacted again.
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn danger-solid" onClick={erase}>Yes, erase everything</button>
            <button className="btn ghost" onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      )}
      {message && <p className="sub" role="status">{message}</p>}
    </section>
  );
}

// "Where this lead came from": every time they were seen, with the campaign, ad or form behind it.
const ATTRIBUTION_LABELS = [["campaign_name", "Campaign"], ["campaign_id", "Campaign id"], ["adset_name", "Ad set"], ["adset_id", "Ad set id"],
  ["ad_name", "Ad"], ["ad_id", "Ad id"], ["form_name", "Form"], ["form_id", "Form id"], ["utm_source", "UTM source"], ["utm_medium", "UTM medium"],
  ["utm_campaign", "UTM campaign"], ["utm_content", "UTM content"], ["utm_term", "UTM term"], ["platform", "Platform"], ["click_id", "Click id"],
  ["landing_url", "Landing page"], ["referrer", "Came from"]];

export function LeadOrigin({ lead }) {
  useSourceRegistry();
  const touches = (lead.sources || []).filter((t) => t && t.id);
  if (!touches.length && !lead.inbound) return null;
  return (
    <section aria-labelledby="origin-title" data-testid="lead-origin">
      <h3 id="origin-title">Where this lead came from</h3>
      <ol className="origin-list">
        {touches.map((t, i) => (
          <li key={i}>
            <b>{sourceLabel(t.id)}</b>{t.detail ? ` · ${t.detail}` : ""} <small className="sub-inline">{t.at ? istLabel(t.at) : ""}{i === 0 ? " · first seen" : ""}</small>
            {t.attribution && (
              <dl className="origin-attrs">
                {ATTRIBUTION_LABELS.filter(([k]) => t.attribution[k]).map(([k, label]) => (
                  <div key={k}><dt>{label}</dt><dd>{t.attribution[k]}</dd></div>
                ))}
              </dl>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

