import { useEffect, useRef, useState } from "react";
import { api, canEditContacts, canWrite, isOwner } from "../api.js";
import { copyText } from "../clipboard.js";
import { sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import { istLabel } from "../time.js";
import LeadMeetings from "./LeadMeetings.jsx";
import LeadActivity from "./LeadActivity.jsx";
import CompanyHealth from "./CompanyHealth.jsx";
import OtherContacts from "./OtherContacts.jsx";
import { ContactPoints, LeadPeople } from "./LeadContacts.jsx";
import LeadPlan from "./LeadPlan.jsx";
import SequencePicker from "./sequences/SequencePicker.jsx";
import { useAlert, useConfirm } from "../Dialog.jsx";
import LeadFlows from "./flows/LeadFlows.jsx";
import LeadStage from "./LeadStage.jsx";
import LeadSales from "./LeadSales.jsx";
import { EditDetails, InlineForm, LeadOrigin, PrivacyActions, SIGNAL_LABELS, linkedinDrafts, replyMeaning, sequenceStatus } from "./LeadDrawerParts.jsx";
import LeadAssign from "./LeadAssign.jsx";
import LinkedInAccountPick from "./LinkedInAccountPick.jsx";
import { LeadCustomData } from "./LeadCustomFields.jsx";
import PhoneTag, { isLandline } from "./PhoneTag.jsx";
import { LeadAuditPack, LeadConsent } from "./LeadAudit.jsx";

// The lead drawer is a header (name, score, stage, prev/next) over five tabs. HOW TO ADD A SMALL PANEL:
//   - lead-level facts / ownership / tags / other contacts  -> the OVERVIEW tab, at the "SLOT: overview panels" line;
//   - anything about sending (sequence choice, channels)     -> the OUTREACH tab, at the "SLOT: outreach panels" line.
// Each panel is one line, e.g. `<LeadOwner lead={lead} onChanged={load} />`; `load()` re-reads the lead and
// `setLead(saved)` swaps in a lead an API call returned.
const TABS = [["overview", "Overview"], ["activity", "Activity"], ["outreach", "Outreach"], ["product", "Product / Free trial"], ["data", "Data"]];
const TAB_KEY = "lead-drawer-tab";
const savedTab = () => { try { const t = localStorage.getItem(TAB_KEY); return TABS.some(([id]) => id === t) ? t : "overview"; } catch { return "overview"; } };
const typing = (el) => el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);

// `ids` (optional): the ordered lead ids of the list the drawer was opened from; enables Prev / Next and the J / K keys.
export default function LeadDrawer({ id, ids, onClose }) {
  useSourceRegistry();
  const [current, setCurrent] = useState(id);
  const [lead, setLead] = useState(null);
  const [tab, setTabState] = useState(savedTab);
  const [note, setNote] = useState("");
  const [reply, setReply] = useState("");
  const [detected, setDetected] = useState(null);
  const [demoUrl, setDemoUrl] = useState("");
  const [waTemplate, setWaTemplate] = useState("first_after_yes");
  const [waText, setWaText] = useState("");
  const [waMode, setWaMode] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState("");          // which inline form is open: pilot / referral
  const confirm = useConfirm();
  const say = useAlert();
  const panel = useRef(null);
  const previousFocus = useRef(null);
  const nav = useRef({});

  useEffect(() => { setCurrent(id); }, [id]);
  const load = () => api.lead(current).then(setLead).catch((e) => setError(e.message));
  useEffect(() => { setLead(null); setError(""); setDetected(null); setDraft(null); setDemoUrl(""); setForm(""); load(); }, [current]);
  useEffect(() => { api.whatsappStatus().then(setWaMode).catch(() => setWaMode(null)); }, []);
  const setTab = (t) => { setTabState(t); try { localStorage.setItem(TAB_KEY, t); } catch { /* storage blocked */ } };

  const list = Array.isArray(ids) ? ids : [];
  const at = list.indexOf(current);
  const prevId = at > 0 ? list[at - 1] : null;
  const nextId = at >= 0 && at < list.length - 1 ? list[at + 1] : null;
  nav.current = { prevId, nextId };

  useEffect(() => {
    previousFocus.current = document.activeElement;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (!typing(e.target) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === "j" && nav.current.nextId) { e.preventDefault(); setCurrent(nav.current.nextId); }
        if (e.key === "k" && nav.current.prevId) { e.preventDefault(); setCurrent(nav.current.prevId); }
      }
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
  const s = lead.suggestion;
  const id_ = lead.id;

  const overview = (
    <>
      <p className="sub">{lead.contact_name} {lead.title && `· ${lead.title}`}<br />{lead.email} {lead.phone && `· ${lead.phone}`}<PhoneTag lead={lead} />
        <br /><small>Source: {sourceLabel(lead)}</small></p>
      {canWrite() && canEditContacts() && <EditDetails lead={lead} onSaved={(r) => setLead(r)} />}
      <ContactPoints lead={lead} onLead={setLead} />
      <div className="tip">
        <b>Suggested next step ({s.priority} priority, via {s.channel}):</b> {s.action}
        {s.pitch && <p style={{ margin: "6px 0 0" }}><b>What to say:</b> {s.pitch}</p>}
      </div>
      <LeadStage lead={lead} onLead={setLead} onError={setError} />
      <LeadSales lead={lead} onChanged={load} />
      <LeadPlan lead={lead} />
      {/* SLOT: overview panels (LeadOwner, LeadTags, OtherContacts …) — one line each */}
      <LeadConsent lead={lead} />
      <LeadAssign lead={lead} onChanged={setLead} />
      <LeadPeople lead={lead} onLead={setLead} />
      <OtherContacts lead={lead} />
      <LeadMeetings lead={lead} onChanged={load} />
      {canWrite() && <>
        <label htmlFor="note-box">Quick note</label>
        <textarea id="note-box" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn" style={{ marginTop: 8 }} disabled={!note.trim()}
          onClick={() => act(async () => { const r = await api.note(id_, note); setNote(""); return r; })}>Save note</button>
      </>}
    </>
  );

  const activity = (
    <>
      <LeadActivity lead={lead} onChanged={load} />
      <h3>Conversation & history</h3>
      <div className="timeline">
        {[...lead.timeline].reverse().map((t, i) => (
          <div key={i} className={t.kind === "edit" ? "timeline-edit" : undefined}><small>{istLabel(t.at)} · {t.kind === "edit" ? "details edited" : t.kind}{t.by ? ` · ${t.by}` : ""}{t.ai_drafted ? " · " : ""}{t.ai_drafted && <span className="pill">AI-drafted</span>}</small>{t.text}</div>
        ))}
      </div>
    </>
  );

  const waHeading = !waMode ? "WhatsApp" : waMode.mode === "live" ? `WhatsApp (live${waMode.provider ? ` via ${waMode.provider}` : ""} — messages really go)`
    : `WhatsApp (practice mode — ${waMode.connected ? "real sending is off" : "no provider connected"}, nothing is really sent)`;
  const outreach = (
    <>
      {/* SLOT: outreach panels (SequencePicker …) — one line each */}
      <h3>Email sequence</h3>
      {lead.sequence ? (
        <div><p className="sub">{sequenceStatus(lead.sequence)}</p>
          {!lead.sequence.stopped && canWrite() && <button className="btn ghost" onClick={async () => (await confirm({ message: `Send ${lead.contact_name || lead.email} their next sequence email now? It goes at once (real email if real sending is on).`, confirmLabel: "Send now" }))
            && act(async () => { const r = await api.emailNow(id_); say(`${r.mode === "live" ? "Sent" : "Practised (not sent)"}: ${r.step} → ${r.to}`); return r.lead; })}>Send email now</button>}</div>
      ) : canWrite() ? (
        <SequencePicker lead={lead} onStart={(sequenceId) => act(() => api.enrollIn(id_, sequenceId))} />
      ) : <p className="sub">Not on an email sequence.</p>}
      {canWrite() && <>
      <label htmlFor="reply-box">Paste their reply (stops the sequence, detects yes / no / question)</label>
      <div className="row">
        <input id="reply-box" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Yes please send the story" />
        <button className="btn" disabled={!reply.trim()} onClick={() => act(async () => {
          const r = await api.reply(id_, reply); setReply(""); setDetected(r); return r.lead;
        })}>Log reply</button>
      </div>
      {detected && (
        <p className="sub" role="status">
          Detected: <b>{detected.label}</b> — {replyMeaning(detected.label, detected.effect)}
          {detected.ai_label && ` (The AI thinks this is "${detected.ai_label}"; check it before acting.)`}
        </p>
      )}
      <button className="btn ghost" disabled={!reply.trim()} onClick={async () => {
        try { setDraft(await api.draftReply(id_, reply)); } catch (e) { setError(e.message); }
      }}>Suggest a reply</button>
      {draft && (
        <div className="tip" style={{ marginTop: 8 }}>
          <b>Draft ({draft.source}, they sound: {draft.label})</b>
          {draft.escalate
            ? <p><b>Needs you:</b> {draft.escalate_reason || draft.note}</p>
            : draft.draft ? <pre>{draft.draft}</pre> : <p>No reply needed for this one.</p>}
          {draft.needs_demo_link && <p>Create the demo link first (Product tab), then ask for a new draft.</p>}
          <small>{draft.note}</small>
          {draft.draft && !draft.escalate && (
            <div><button className="btn ghost" style={{ marginTop: 6 }} onClick={() => copyText(draft.draft)}>Copy draft</button></div>
          )}
        </div>
      )}
      </>}

      <h3 data-testid="wa-heading">{waHeading}</h3>
      <p className="sub">Only after they said yes to WhatsApp, and only to a mobile number. Free text only within 24 hours of their last message.</p>
      {isLandline(lead) && <div className="tip" data-testid="wa-landline">This lead's phone is a landline: call it, but WhatsApp and SMS are not sent to it.</div>}
      {canWrite() && <>
      <div className="row">
        <select aria-label="WhatsApp message" value={waTemplate} onChange={(e) => setWaTemplate(e.target.value)} style={{ flex: 1 }}>
          <option value="first_after_yes">First message after yes</option>
          <option value="first_after_yes_hinglish">First message after yes (Hinglish)</option>
          <option value="nudge_demo_not_opened">Nudge: demo not opened</option>
          <option value="nudge_not_published">Nudge: draft not published</option>
          <option value="pilot_offer">Free pilot offer</option>
          <option value="custom">Write my own</option>
        </select>
        <button className="btn ghost" onClick={() => act(async () => { const r = await api.sendWhatsapp(id_, waTemplate, waText); setWaText("");
          say(waMode?.mode === "live" ? "WhatsApp sent." : "Practised (not sent): the WhatsApp message is in Outbox → WhatsApp messages."); return r; })}>Send</button>
      </div>
      {waTemplate === "custom" && <textarea aria-label="Your WhatsApp message" rows={2} style={{ marginTop: 8 }} value={waText} onChange={(e) => setWaText(e.target.value)} placeholder="Your message" />}
      </>}

      <h3>LinkedIn</h3>
      <p className="sub">{lead.linkedin_seq
        ? `On the LinkedIn sequence: ${String(lead.linkedin_seq.status || "").replace(/_/g, " ")}${(lead.linkedin_seq.done || []).length ? `, steps done ${lead.linkedin_seq.done.join(", ")}` : ""}${lead.linkedin_seq.variant ? `, note ${lead.linkedin_seq.variant}` : ""}${lead.linkedin_seq.account ? `, handled by ${lead.linkedin_seq.account}` : ""}. The LinkedIn agent or a person sends each step from the LinkedIn page.`
        : "Not on the LinkedIn sequence. Start it from Leads (select, then Start LinkedIn outreach), or copy a draft below and send it yourself."}
        {/^https?:\/\//i.test(lead.linkedin_url || "") && <> <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer">Open profile</a></>}
        {lead.linkedin_profile?.open_profile && <span className="pill" style={{ marginLeft: 6 }}>Open Profile</span>}</p>
      <LinkedInAccountPick lead={lead} onChanged={load} />
      {linkedinDrafts(lead).map(([title, text]) => (
        <div className="tip" key={title} style={{ marginBottom: 8 }}>
          <b>{title}</b> <small>({text.length} characters)</small>
          <p style={{ margin: "4px 0" }}>{text}</p>
          <button className="btn ghost" onClick={() => copyText(text)}>Copy</button>
        </div>
      ))}
      <LeadFlows lead={lead} onChanged={load} />
    </>
  );

  const product = (
    <>
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
      {canWrite() && <>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn ghost" onClick={async () => {
          try {
            const l = await api.demoLink(id_);
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
          onSubmit={(v) => act(() => api.pilot(id_, Number(v.applicants)))} />
      )}
      {form === "referral" && (
        <InlineForm submitLabel="Save referral" onCancel={() => setForm("")}
          fields={[{ key: "company", label: "Which company do they recommend?", required: true },
                   { key: "email", label: "Contact email (optional)", type: "email" }]}
          onSubmit={(v) => act(async () => { await api.referral(id_, v); return api.lead(id_); })} />
      )}
      {demoUrl && <p className="sub">Copied: {demoUrl}</p>}
      <label>Record what they did in the product
        <select defaultValue="" onChange={(e) => {
          if (!e.target.value) return;
          const [event, count] = e.target.value.split(":");
          act(async () => { await api.activationEvent(id_, event, Number(count)); return api.lead(id_); });
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
      </>}
    </>
  );

  const data = (
    <>
      <LeadCustomData lead={lead} onSaved={(r) => setLead(r)} />
      <h3>Buying signals — {lead.score}/100 ({lead.band})</h3>
      {Object.entries(SIGNAL_LABELS).map(([key, [label, max]]) => (
        <div className="funnel-row" key={key}>
          <span>{label}</span>
          <div className="bar" role="img" aria-label={`${lead.signals[key]} of ${max}`} style={{ width: `${(lead.signals[key] / max) * 100}%` }} />
          <small>{lead.signals[key]}/{max}</small>
        </div>
      ))}
      <LeadOrigin lead={lead} />
      <CompanyHealth lead={lead} />
      <LeadAuditPack lead={lead} />
      {isOwner() && <PrivacyActions lead={lead} onErased={onClose} />}
    </>
  );

  const body = { overview, activity, outreach, product, data }[tab] || overview;
  return shell(
    <>
      <div className="drawer-head">
        <div style={{ minWidth: 0 }}>
          <h2 id="drawer-title" tabIndex={-1}>{lead.company || lead.domain || lead.contact_name}</h2>
          <span className={`pill ${lead.band}`}>{lead.score} · {lead.band}</span> <span className="pill" data-testid="drawer-stage">{stageLabel(lead.stage)}</span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
          {list.length > 1 && at >= 0 && (
            <>
              <button className="btn ghost" disabled={!prevId} onClick={() => setCurrent(prevId)} aria-label="Previous lead (K)" title="Previous lead (K)">‹</button>
              <small className="sub" style={{ margin: 0, whiteSpace: "nowrap" }} aria-live="polite">{at + 1} / {list.length}</small>
              <button className="btn ghost" disabled={!nextId} onClick={() => setCurrent(nextId)} aria-label="Next lead (J)" title="Next lead (J)">›</button>
            </>
          )}
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
      </div>
      <div className="tabs scroll" role="tablist" aria-label="Lead sections">
        {TABS.map(([tid, label]) => (
          <button key={tid} role="tab" aria-selected={tab === tid} className={tab === tid ? "on" : ""} onClick={() => setTab(tid)}>{label}</button>
        ))}
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div role="tabpanel" aria-label={TABS.find(([tid]) => tid === tab)?.[1]}>{body}</div>
    </>
  );
}
