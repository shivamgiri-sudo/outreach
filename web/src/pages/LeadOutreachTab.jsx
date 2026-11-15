// The lead drawer's Outreach tab: email sequence, replies, WhatsApp, LinkedIn and flows, one card each.
import { useEffect, useState } from "react";
import { api, canWrite, isOwner, isWorkspaceAdmin } from "../api.js";
import { copyText } from "../clipboard.js";
import { useAlert, useConfirm } from "../Dialog.jsx";
import SequencePicker from "./sequences/SequencePicker.jsx";
import LeadFlows from "./flows/LeadFlows.jsx";
import { LeadAssignedBy } from "./LeadFirstTouch.jsx";
import LinkedInAccountPick from "./LinkedInAccountPick.jsx";
import LinkedInFinder from "./LinkedInFinder.jsx";
import LinkedInContacts from "./LinkedInContacts.jsx";
import LinkedInWhy from "./LinkedInWhy.jsx";
import LinkedInStartStop from "./LinkedInStartStop.jsx";
import { linkedinDrafts, replyMeaning, sequenceStatus } from "./LeadDrawerParts.jsx";
import PhoneTag, { isLandline } from "./PhoneTag.jsx";
import { Card, CardHeader, Button, Badge } from "../ui/index.js";

export default function LeadOutreachTab({ lead, act, setLead, setError, load }) {
  const id_ = lead.id;
  const confirm = useConfirm();
  const say = useAlert();
  const [reply, setReply] = useState("");
  const [detected, setDetected] = useState(null);
  const [draft, setDraft] = useState(null);
  const [waTemplate, setWaTemplate] = useState("first_after_yes");
  const [waText, setWaText] = useState("");
  const [waMode, setWaMode] = useState(null);
  useEffect(() => { api.whatsappStatus().then(setWaMode).catch(() => setWaMode(null)); }, []);

  const waHeading = !waMode ? "WhatsApp" : waMode.mode === "live" ? `WhatsApp (live${waMode.provider ? ` via ${waMode.provider}` : ""} — messages really go)`
    : `WhatsApp (practice mode — ${waMode.connected ? "real sending is off" : "no provider connected"}, nothing is really sent)`;
  return (
    <>
      {/* SLOT: outreach panels (SequencePicker …) — one line each */}
      <Card flat className="dsec">
        <LeadAssignedBy lead={lead} />
        <CardHeader title="Email sequence" />
        {lead.sequence ? (
          <div><p className="sub">{sequenceStatus(lead.sequence)}</p>
            {!lead.sequence.stopped && canWrite() && <Button variant="secondary" onClick={async () => (await confirm({ message: `Send ${lead.contact_name || lead.email} their next sequence email now? It goes at once (real email if real sending is on).`, confirmLabel: "Send now" }))
              && act(async () => { const r = await api.emailNow(id_); say(`${r.mode === "live" ? "Sent" : "Practised (not sent)"}: ${r.step} → ${r.to}`); return r.lead; })}>Send email now</Button>}</div>
        ) : canWrite() ? (
          <SequencePicker lead={lead} onStart={(sequenceId) => act(() => api.enrollIn(id_, sequenceId))} />
        ) : <p className="sub">Not on an email sequence.</p>}
        {canWrite() && <>
          <label htmlFor="reply-box">Paste their reply (stops the sequence, detects yes / no / question)</label>
          <div className="row nowrap-row">
            <input id="reply-box" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Yes please send the story" />
            <Button disabled={!reply.trim()} onClick={() => act(async () => {
              const r = await api.reply(id_, reply); setReply(""); setDetected(r); return r.lead;
            })}>Log reply</Button>
          </div>
          {detected && (
            <p className="sub" role="status">
              Detected: <b>{detected.label}</b> — {replyMeaning(detected.label, detected.effect)}
              {detected.ai_label && ` (The AI thinks this is "${detected.ai_label}"; check it before acting.)`}
            </p>
          )}
          <div className="u-mt-8"><Button variant="secondary" disabled={!reply.trim()} onClick={async () => {
            try { setDraft(await api.draftReply(id_, reply)); } catch (e) { setError(e.message); }
          }}>Suggest a reply</Button></div>
          {draft && (
            <div className="tip u-mt-8">
              <b>Draft ({draft.source}, they sound: {draft.label})</b>
              {draft.escalate
                ? <p><b>Needs you:</b> {draft.escalate_reason || draft.note}</p>
                : draft.draft ? <pre>{draft.draft}</pre> : <p>No reply needed for this one.</p>}
              {draft.needs_demo_link && <p>Create the demo link first (Product tab), then ask for a new draft.</p>}
              <small>{draft.note}</small>
              {draft.draft && !draft.escalate && (
                <div><Button variant="secondary" className="u-mt-8" onClick={() => copyText(draft.draft)}>Copy draft</Button></div>
              )}
            </div>
          )}
        </>}
      </Card>

      <Card flat className="dsec">
        <CardHeader title={<span data-testid="wa-heading">{waHeading}</span>} description="Only after they said yes to WhatsApp, and only to a mobile number. Free text only within 24 hours of their last message." />
        {isLandline(lead) && <div className="tip u-mb-8" data-testid="wa-landline">This lead's phone is a landline: call it, but WhatsApp and SMS are not sent to it.</div>}
        {canWrite() && <>
          <div className="row nowrap-row">
            <select aria-label="WhatsApp message" value={waTemplate} onChange={(e) => setWaTemplate(e.target.value)} className="u-flex1">
              <option value="first_after_yes">First message after yes</option>
              <option value="first_after_yes_hinglish">First message after yes (Hinglish)</option>
              <option value="nudge_demo_not_opened">Nudge: demo not opened</option>
              <option value="nudge_not_published">Nudge: draft not published</option>
              <option value="pilot_offer">Free pilot offer</option>
              <option value="custom">Write my own</option>
            </select>
            <Button variant="secondary" onClick={() => act(async () => { const r = await api.sendWhatsapp(id_, waTemplate, waText); setWaText("");
              say(waMode?.mode === "live" ? "WhatsApp sent." : "Practised (not sent): the WhatsApp message is in Outbox → WhatsApp messages."); return r; })}>Send</Button>
          </div>
          {waTemplate === "custom" && <textarea aria-label="Your WhatsApp message" rows={2} className="u-mt-8" value={waText} onChange={(e) => setWaText(e.target.value)} placeholder="Your message" />}
        </>}
      </Card>

      <Card flat className="dsec">
        <CardHeader title="LinkedIn" />
        <p className="sub">{lead.linkedin_seq
          ? `On the LinkedIn sequence: ${String(lead.linkedin_seq.status || "").replace(/_/g, " ")}${(lead.linkedin_seq.done || []).length ? `, steps done ${lead.linkedin_seq.done.join(", ")}` : ""}${lead.linkedin_seq.variant ? `, note ${lead.linkedin_seq.variant}` : ""}${lead.linkedin_seq.account ? `, handled by ${lead.linkedin_seq.account}` : ""}. The LinkedIn agent or a person sends each step from the LinkedIn page.`
          : "Not on the LinkedIn sequence yet. Press Start LinkedIn outreach below, or copy a draft and send it yourself."}
          {/^https?:\/\//i.test(lead.linkedin_url || "") && <> <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer">Open profile</a></>}
          {lead.linkedin_profile?.open_profile && <Badge className="u-ml-8">Open Profile</Badge>}</p>
        {!lead.linkedin_seq && lead.linkedin_dup_of && <p className="sub lead-warn" data-testid="linkedin-dup">
          {lead.linkedin_dup_reason || "This person was already contacted on LinkedIn through another lead."} It will not be contacted again.</p>}
        {!lead.linkedin_seq && lead.linkedin_lookup && ["queued", "ambiguous", "not_found", "manual"].includes(lead.linkedin_lookup.status) && !lead.linkedin_lookup.skipped_by
          && <LinkedInFinder row={{ lead_id: lead.id, name: lead.contact_name, company: lead.company, ...lead.linkedin_lookup }} onChanged={load} onMessage={say} />}
        <LinkedInStartStop lead={lead} onLead={setLead} say={say} />
        <LinkedInContacts lead={lead} />
        {(isOwner() || isWorkspaceAdmin()) && <LinkedInWhy lead={lead} />}
        <LinkedInAccountPick lead={lead} onChanged={load} />
        {linkedinDrafts(lead).map(([title, text]) => (
          <div className="tip u-mb-8" key={title}>
            <b>{title}</b> <small>({text.length} characters)</small>
            <p className="u-m-4-0">{text}</p>
            <Button variant="secondary" onClick={() => copyText(text)}>Copy</Button>
          </div>
        ))}
      </Card>
      <LeadFlows lead={lead} onChanged={load} />
    </>
  );
}
