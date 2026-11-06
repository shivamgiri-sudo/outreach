import { useEffect, useRef, useState } from "react";
import { api, canEditContacts, isOwner } from "../api.js";
import { copyText } from "../clipboard.js";
import { SEQUENCE_STOPPED, sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import { istLabel } from "../time.js";
import LeadMeetings from "./LeadMeetings.jsx";
import LeadActivity from "./LeadActivity.jsx";
import CompanyHealth from "./CompanyHealth.jsx";
import OtherContacts from "./OtherContacts.jsx";
import LeadFlows from "./flows/LeadFlows.jsx";
import SequencePicker from "./sequences/SequencePicker.jsx";
import { useAlert, useConfirm } from "../Dialog.jsx";

const SIGNAL_LABELS = {
  hiring_volume: ["Hiring volume", 30], hiring_pain: ["Hiring pain", 25], fit: ["Fit", 15],
  engagement: ["Engagement", 20], intent: ["Intent", 10],
};
const FORWARD = {
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

function replyMeaning(label, effect) {
  if (effect === "review") return "the lead is past this stage, so nothing was changed automatically. A task was created for you to check.";
  return REPLY_MEANING[label] || "logged.";
}

// Short, honest drafts for a person to send on LinkedIn (connection notes are limited to 300 characters).
function linkedinDrafts(lead) {
  const first = (lead.contact_name || "").replace(/^(mr|mrs|ms|dr)\.?\s+/i, "").split(" ")[0];
  const hi = first ? `Hi ${first}` : "Hi";
  const company = (lead.company || lead.domain || "your team").replace(/\s*\(.*?\)/g, "").replace(/\s+(pvt|private|ltd|limited|llp)\.?/gi, "").trim();
  const role = lead.role ? lead.role.toLowerCase() : "";
  const hiring = role ? `hiring a ${role}` : "hiring";
  return [
    ["Connection note", `${hi}, I saw ${company} is ${hiring}. I work on BookMyInterview, which screens applicants once so you only interview the right few. Happy to connect.`.slice(0, 300)],
    ["First message (after they accept)", `${hi}, thanks for connecting. If ${role || "your open role"} is bringing more CVs than you can read, I can draft a free sample screen for it. Want me to send it? No pressure either way.`],
    ["Comment on their hiring post", `Lots of CVs and only a few right fits is the usual story for roles like this. Three short role-specific questions before any call help a lot. Happy to share a sample if useful.`],
  ];
}

function sequenceStatus(seq) {
  if (seq.stopped) return `Stopped (${seq.stopped}) — ${SEQUENCE_STOPPED[seq.stopped] || "no more emails"}`;
  if (seq.in_flight) return "Needs a check: the app stopped mid-send. Look in the outbox before anything else is sent.";
  if (seq.blocked) return `Waiting: ${seq.blocked}.`;
  return `Running — steps sent on day(s): ${seq.sent_days.join(", ") || "none yet"}`;
}

// A small inline form instead of a browser pop-up: labelled fields, Save and Cancel.
function InlineForm({ fields, submitLabel, onSubmit, onCancel }) {
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

function EditDetails({ lead, onSaved }) {
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
function PrivacyActions({ lead, onErased }) {
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

function LeadOrigin({ lead }) {
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

export default function LeadDrawer({ id, onClose }) {
  const confirm = useConfirm();
  const say = useAlert();
  useSourceRegistry();
  const [lead, setLead] = useState(null);
  const [note, setNote] = useState("");
  const [reply, setReply] = useState("");
  const [detected, setDetected] = useState(null);
  const [demoUrl, setDemoUrl] = useState("");
  const [waTemplate, setWaTemplate] = useState("first_after_yes");
  const [waText, setWaText] = useState("");
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState("");          // which inline form is open: consent / pilot / referral / unsubscribe
  const panel = useRef(null);
  const previousFocus = useRef(null);

  const load = () => api.lead(id).then(setLead).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [id]);

  useEffect(() => {
    previousFocus.current = document.activeElement;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = panel.current.querySelectorAll("button, input, select, textarea, a[href]");
      if (!focusable.length) return;
      const [firstEl, lastEl] = [focusable[0], focusable[focusable.length - 1]];
      if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
      if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); previousFocus.current?.focus?.(); };
  }, []);
  useEffect(() => { if (lead) panel.current?.querySelector("h2")?.focus(); }, [lead?.id]);

  const act = async (fn) => {
    setError("");
    try { setLead(await fn()); setForm(""); } catch (e) { setError(e.message); }
  };

  const shell = (content) => (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" ref={panel}>{content}</div>
    </>
  );
  if (!lead) return shell(<p>{error || "Loading…"}</p>);
  const next = FORWARD[lead.stage];
  const s = lead.suggestion;

  return shell(
    <>
      <div className="drawer-head">
        <div>
          <h2 id="drawer-title" tabIndex={-1}>{lead.company || lead.domain || lead.contact_name}</h2>
          <span className={`pill ${lead.band}`}>{lead.score} · {lead.band}</span> <span className="pill">{stageLabel(lead.stage)}</span>
        </div>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      <p className="sub">{lead.contact_name} {lead.title && `· ${lead.title}`}<br />{lead.email} {lead.phone && `· ${lead.phone}`}
        <br /><small>Source: {sourceLabel(lead)}</small></p>
      {canEditContacts() && <EditDetails lead={lead} onSaved={(r) => setLead(r)} />}
      {error && <div className="tip error" role="alert">{error}</div>}

      <LeadOrigin lead={lead} />

      <div className="tip">
        <b>Suggested next step ({s.priority} priority, via {s.channel}):</b> {s.action}
        {s.pitch && <p style={{ margin: "6px 0 0" }}><b>What to say:</b> {s.pitch}</p>}
      </div>

      <h3>Buying signals — {lead.score}/100 ({lead.band})</h3>
      {Object.entries(SIGNAL_LABELS).map(([key, [label, max]]) => (
        <div className="funnel-row" key={key}>
          <span>{label}</span>
          <div className="bar" role="img" aria-label={`${lead.signals[key]} of ${max}`} style={{ width: `${(lead.signals[key] / max) * 100}%` }} />
          <small>{lead.signals[key]}/{max}</small>
        </div>
      ))}

      <h3>Stage: {stageLabel(lead.stage)}</h3>
      <div className="row">
        {next && <button className="btn" onClick={() => act(() => api.moveStage(id, next))}>Move to {stageLabel(next)}</button>}
        {!lead.consent && <button className="btn ghost" onClick={() => setForm("consent")}>Record “yes” consent</button>}
        <button className="btn ghost" onClick={() => act(() => api.moveStage(id, "not_now"))}>Not now</button>
        {lead.stage !== "unsubscribed" && <button className="btn ghost danger" onClick={() => setForm("unsubscribe")}>Unsubscribed</button>}
      </div>
      {form === "consent" && (
        <InlineForm submitLabel="Save consent" onCancel={() => setForm("")}
          fields={[
            { key: "text", label: "Their exact words, and where (saved as proof of consent)", required: true,
              placeholder: "e.g. “Yes, send it on WhatsApp” — call with Amit, 3 Nov" },
            { key: "channel", label: "They agreed to be contacted on", initial: "whatsapp",
              options: [["whatsapp", "WhatsApp"], ["call", "Phone call"], ["email", "Email"], ["any", "Any channel"]] },
          ]}
          onSubmit={(v) => act(() => api.consent(id, v.text, v.channel))} />
      )}
      {form === "unsubscribe" && (
        <div className="tip error" role="alert">
          This stops every email and WhatsApp to them for good and cannot be undone here.
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn danger-solid" onClick={() => act(() => api.moveStage(id, "unsubscribed"))}>Yes, unsubscribe them</button>
            <button className="btn ghost" onClick={() => setForm("")}>Cancel</button>
          </div>
        </div>
      )}
      {lead.consent && <p className="sub">Consent saved: “{lead.consent.text}” on {istLabel(lead.consent.at)}</p>}

      <LeadMeetings lead={lead} onChanged={load} />
      <CompanyHealth lead={lead} />
      <OtherContacts lead={lead} />
      <LeadActivity lead={lead} onChanged={load} />
      <LeadFlows lead={lead} onChanged={load} />
      {isOwner() && <PrivacyActions lead={lead} onErased={onClose} />}

      <h3>Free experience</h3>
      {lead.activation ? (
        <>
          <p className="sub">
            Step: <b>{lead.activation.step}</b> · candidates screened {lead.activation.usage.candidates}, interviews booked {lead.activation.usage.bookings}
            {lead.bonus_candidates ? ` · +${lead.bonus_candidates} referral bonus` : ""}
          </p>
          {lead.nudge && <div className="tip">{lead.nudge.text}</div>}
        </>
      ) : <p className="sub">No demo link yet.</p>}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn ghost" onClick={async () => {
          try {
            const l = await api.demoLink(id);
            const ok = await copyText(l.url);
            setDemoUrl(ok ? l.url : `${l.url} (copy it from here)`); load();
          } catch (e) { setError(e.message); }
        }}>{lead.demo_token ? "Copy demo link" : "Create demo link"}</button>
        <button className="btn ghost" onClick={() => setForm("pilot")}>Offer free pilot on their applicants</button>
        <button className="btn ghost" onClick={() => setForm("referral")}>Log a referral</button>
      </div>
      {form === "pilot" && (
        <InlineForm submitLabel="Log pilot" onCancel={() => setForm("")}
          fields={[{ key: "applicants", label: "How many applicants do they already have?", type: "number", initial: "100", required: true }]}
          onSubmit={(v) => act(() => api.pilot(id, Number(v.applicants)))} />
      )}
      {form === "referral" && (
        <InlineForm submitLabel="Save referral" onCancel={() => setForm("")}
          fields={[{ key: "company", label: "Which company do they recommend?", required: true },
                   { key: "email", label: "Contact email (optional)", type: "email" }]}
          onSubmit={(v) => act(async () => { await api.referral(id, v); return api.lead(id); })} />
      )}
      {demoUrl && <p className="sub">Copied: {demoUrl}</p>}
      <label>Record what they did in the product
        <select defaultValue="" onChange={(e) => {
          if (!e.target.value) return;
          const [event, count] = e.target.value.split(":");
          act(async () => { await api.activationEvent(id, event, Number(count)); return api.lead(id); });
          e.target.value = "";
        }}>
          <option value="">Choose…</option>
          <option value="role_drafted:1">Drafted a role</option>
          <option value="role_published:1">Published the role</option>
          <option value="candidate_screened:1">1 candidate screened</option>
          <option value="candidate_screened:5">5 candidates screened</option>
          <option value="interview_booked:1">1 interview booked</option>
          <option value="converted:1">Became a paying customer</option>
        </select>
      </label>

      <h3>LinkedIn</h3>
      <p className="sub">{lead.linkedin_seq
        ? `On the LinkedIn sequence: ${String(lead.linkedin_seq.status || "").replace(/_/g, " ")}${(lead.linkedin_seq.done || []).length ? `, steps done ${lead.linkedin_seq.done.join(", ")}` : ""}${lead.linkedin_seq.variant ? `, note ${lead.linkedin_seq.variant}` : ""}${lead.linkedin_seq.account ? `, handled by ${lead.linkedin_seq.account}` : ""}. The LinkedIn agent or a person sends each step from the LinkedIn page.`
        : "Not on the LinkedIn sequence. Start it from Leads (select, then Start LinkedIn outreach), or copy a draft below and send it yourself."}
        {/^https?:\/\//i.test(lead.linkedin_url || "") && <> <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer">Open profile</a></>}
        {lead.linkedin_profile?.open_profile && <span className="pill" style={{ marginLeft: 6 }}>Open Profile</span>}</p>
      {linkedinDrafts(lead).map(([title, text]) => (
        <div className="tip" key={title} style={{ marginBottom: 8 }}>
          <b>{title}</b> <small>({text.length} characters)</small>
          <p style={{ margin: "4px 0" }}>{text}</p>
          <button className="btn ghost" onClick={() => copyText(text)}>Copy</button>
        </div>
      ))}

      <h3>WhatsApp (practice mode)</h3>
      <p className="sub">Only after they said yes, and only to a mobile number. Free text only within 24 hours of their last message.</p>
      <div className="row">
        <select aria-label="WhatsApp message" value={waTemplate} onChange={(e) => setWaTemplate(e.target.value)} style={{ flex: 1 }}>
          <option value="first_after_yes">First message after yes</option>
          <option value="first_after_yes_hinglish">First message after yes (Hinglish)</option>
          <option value="nudge_demo_not_opened">Nudge: demo not opened</option>
          <option value="nudge_not_published">Nudge: draft not published</option>
          <option value="pilot_offer">Free pilot offer</option>
          <option value="custom">Write my own</option>
        </select>
        <button className="btn ghost" onClick={() => act(async () => { const r = await api.sendWhatsapp(id, waTemplate, waText); setWaText(""); return r; })}>Send</button>
      </div>
      {waTemplate === "custom" && <textarea aria-label="Your WhatsApp message" rows={2} style={{ marginTop: 8 }} value={waText} onChange={(e) => setWaText(e.target.value)} placeholder="Your message" />}

      <h3>Email sequence</h3>
      {lead.sequence ? (
        <div><p className="sub">{sequenceStatus(lead.sequence)}</p>
          {!lead.sequence.stopped && <button className="btn ghost" onClick={async () => (await confirm({ message: `Send ${lead.contact_name || lead.email} their next sequence email now? It goes at once (real email if real sending is on).`, confirmLabel: "Send now" }))
            && act(async () => { const r = await api.emailNow(id); say(`${r.mode === "live" ? "Sent" : "Practised (not sent)"}: ${r.step} → ${r.to}`); return r.lead; })}>Send email now</button>}</div>
      ) : (
        <SequencePicker lead={lead} onStart={(sequenceId) => act(() => api.enrollIn(id, sequenceId))} />
      )}
      <label htmlFor="reply-box">Paste their reply (stops the sequence, detects yes / no / question)</label>
      <div className="row">
        <input id="reply-box" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Yes please send the story" />
        <button className="btn" disabled={!reply.trim()} onClick={() => act(async () => {
          const r = await api.reply(id, reply); setReply(""); setDetected(r); return r.lead;
        })}>Log reply</button>
      </div>
      {detected && (
        <p className="sub" role="status">
          Detected: <b>{detected.label}</b> — {replyMeaning(detected.label, detected.effect)}
          {detected.ai_label && ` (The AI thinks this is "${detected.ai_label}"; check it before acting.)`}
        </p>
      )}
      <button className="btn ghost" disabled={!reply.trim()} onClick={async () => {
        try { setDraft(await api.draftReply(id, reply)); } catch (e) { setError(e.message); }
      }}>Suggest a reply</button>
      {draft && (
        <div className="tip" style={{ marginTop: 8 }}>
          <b>Draft ({draft.source}, they sound: {draft.label})</b>
          {draft.escalate
            ? <p><b>Needs you:</b> {draft.escalate_reason || draft.note}</p>
            : draft.draft ? <pre>{draft.draft}</pre> : <p>No reply needed for this one.</p>}
          {draft.needs_demo_link && <p>Create the demo link above first, then ask for a new draft.</p>}
          <small>{draft.note}</small>
          {draft.draft && !draft.escalate && (
            <div><button className="btn ghost" style={{ marginTop: 6 }} onClick={() => copyText(draft.draft)}>Copy draft</button></div>
          )}
        </div>
      )}

      <h3>Conversation & history</h3>
      <div className="timeline">
        {[...lead.timeline].reverse().map((t, i) => (
          <div key={i} className={t.kind === "edit" ? "timeline-edit" : undefined}><small>{istLabel(t.at)} · {t.kind === "edit" ? "details edited" : t.kind}{t.by ? ` · ${t.by}` : ""}</small>{t.text}</div>
        ))}
      </div>
      <label htmlFor="note-box">Log a reply, call or note</label>
      <textarea id="note-box" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn" style={{ marginTop: 8 }} disabled={!note.trim()}
        onClick={() => act(async () => { const r = await api.note(id, note); setNote(""); return r; })}>Save</button>
    </>
  );
}
