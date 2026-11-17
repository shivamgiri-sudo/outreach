import { useEffect, useRef, useState } from "react";
import { api, canEditContacts, canWrite, isOwner, isWorkspaceAdmin } from "../api.js";
import { sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import { istLabel } from "../time.js";
import LeadMeetings from "./LeadMeetings.jsx";
import LeadActivity from "./LeadActivity.jsx";
import CompanyHealth from "./CompanyHealth.jsx";
import { DomainSignals, FindTheRightPerson } from "./LeadResearch.jsx";
import HiringSignal from "./HiringSignal.jsx";
import CompanyRegisterPanel from "./CompanyRegisterPanel.jsx";
import OtherContacts from "./OtherContacts.jsx";
import { ContactPoints, LeadPeople } from "./LeadContacts.jsx";
import LeadPlan from "./LeadPlan.jsx";
import LeadStage from "./LeadStage.jsx";
import LeadSales from "./LeadSales.jsx";
import { EditDetails, LeadOrigin, PrivacyActions, SIGNAL_LABELS } from "./LeadDrawerParts.jsx";
import LeadOutreachTab from "./LeadOutreachTab.jsx";
import LeadGuess from "./LeadGuess.jsx";
import LeadProductTab from "./LeadProductTab.jsx";
import { Card, CardHeader, Button, Badge, Skeleton } from "../ui/index.js";
import "../lead-drawer.css";
import LeadAssign from "./LeadAssign.jsx";
import { LeadCustomData } from "./LeadCustomFields.jsx";
import PhoneTag from "./PhoneTag.jsx";
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
  const [error, setError] = useState("");
  const panel = useRef(null);
  const previousFocus = useRef(null);
  const nav = useRef({});

  useEffect(() => { setCurrent(id); }, [id]);
  const load = () => api.lead(current).then(setLead).catch((e) => setError(e.message));
  useEffect(() => { setLead(null); setError(""); load(); }, [current]);
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
    try { setLead(await fn()); } catch (e) { setError(e.message); }
  };

  const shell = (content) => (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer lead-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" ref={panel}>{content}</div>
    </>
  );
  if (!lead) return shell(error ? <p className="error">{error}</p> : <div className="stack" aria-busy="true"><Skeleton height={28} width="55%" /><Skeleton height={36} /><Skeleton height={140} /><Skeleton height={140} /></div>);
  const s = lead.suggestion;
  const id_ = lead.id;

  const overview = (
    <>
      <Card flat className="dsec">
        <p className="lead-contact">{lead.email} {lead.phone && `· ${lead.phone}`}<PhoneTag lead={lead} />
          <small className="lead-source">Source: {sourceLabel(lead)}</small></p>
        {canWrite() && canEditContacts() && <EditDetails lead={lead} onSaved={(r) => setLead(r)} />}
      </Card>
      <ContactPoints lead={lead} onLead={setLead} />
      <LeadGuess lead={lead} />
      <div className="tip">
        <b>Suggested next step ({s.priority} priority, via {s.channel}):</b> {s.action}
        {s.pitch && <p className="u-m-8-0-0"><b>What to say:</b> {s.pitch}</p>}
      </div>
      <LeadStage lead={lead} onLead={setLead} onError={setError} />
      <LeadSales lead={lead} onChanged={load} />
      <LeadPlan lead={lead} />
      {/* SLOT: overview panels (LeadOwner, LeadTags, OtherContacts …) — one line each */}
      <LeadConsent lead={lead} />
      <LeadAssign lead={lead} onChanged={setLead} />
      <LeadPeople lead={lead} onLead={setLead} />
      <OtherContacts lead={lead} />
      <FindTheRightPerson lead={lead} onChanged={load} />
      <LeadMeetings lead={lead} onChanged={load} />
      {lead.email_check?.status === "risky" && !lead.email_check.approved && (isOwner() || isWorkspaceAdmin()) && (
        <div className="tip warn">The mailbox check says this address is risky, so the first email waits.{" "}
          <Button variant="secondary" size="sm" onClick={() => act(async () => { await api.approveEmail(id_); return api.lead(id_); })}>Approve this address</Button></div>
      )}
      {canWrite() && <Card flat className="dsec">
        <CardHeader title="Quick note" />
        <label htmlFor="note-box" className="sr-only">Quick note</label>
        <textarea id="note-box" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        <Button className="u-mt-8" disabled={!note.trim()}
          onClick={() => act(async () => { const r = await api.note(id_, note); setNote(""); return r; })}>Save note</Button>
      </Card>}
    </>
  );

  const activity = (
    <>
      <LeadActivity lead={lead} onChanged={load} />
      <Card flat className="dsec">
      <CardHeader title="Conversation & history" />
      <div className="timeline">
        {[...lead.timeline].reverse().map((t, i) => (
          <div key={i} className={t.kind === "edit" ? "timeline-edit" : undefined}><small>{istLabel(t.at)} · {t.kind === "edit" ? "details edited" : t.kind}{t.by ? ` · ${t.by}` : ""}{t.ai_drafted ? " · " : ""}{t.ai_drafted && <span className="pill">AI-drafted</span>}</small>{t.text}</div>
        ))}
      </div>
      </Card>
    </>
  );

  const outreach = <LeadOutreachTab key={lead.id} lead={lead} act={act} setLead={setLead} setError={setError} load={load} />;
  const product = <LeadProductTab key={lead.id} lead={lead} act={act} setError={setError} load={load} />;

  const data = (
    <>
      <LeadCustomData lead={lead} onSaved={(r) => setLead(r)} />
      <Card flat className="dsec">
        <CardHeader title={`Buying signals — ${lead.score}/100 (${lead.band})`} />
        {Object.entries(SIGNAL_LABELS).map(([key, [label, max]]) => (
          <div className="funnel-row" key={key}>
            <span>{label}</span>
            <div className="bar" role="img" aria-label={`${lead.signals[key]} of ${max}`} style={{ width: `${(lead.signals[key] / max) * 100}%` }} />
            <small>{lead.signals[key]}/{max}</small>
          </div>
        ))}
      </Card>
      <LeadOrigin lead={lead} />
      <HiringSignal lead={lead} onChanged={load} />
      <CompanyRegisterPanel lead={lead} onChanged={load} />
      <DomainSignals lead={lead} onChanged={load} />
      <CompanyHealth lead={lead} />
      <Card flat className="dsec"><LeadAuditPack lead={lead} /></Card>
      {isOwner() && <PrivacyActions lead={lead} onErased={onClose} />}
    </>
  );

  const body = { overview, activity, outreach, product, data }[tab] || overview;
  const quick = [
    lead.phone && <a key="call" className="btn ghost sm" href={`tel:${lead.phone}`}>Call</a>,
    lead.email && <a key="mail" className="btn ghost sm" href={`mailto:${lead.email}`}>Email</a>,
    /^https?:\/\//i.test(lead.linkedin_url || "") && <a key="li" className="btn ghost sm" href={lead.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn</a>,
    canWrite() && <Button key="log" size="sm" variant="secondary" onClick={() => setTab("activity")}>Log a call or task</Button>,
  ].filter(Boolean);
  return shell(
    <>
      <div className="lead-top">
        <div className="lead-head">
          <div className="u-min0">
            <h2 id="drawer-title" tabIndex={-1}>{lead.company || lead.domain || lead.contact_name}</h2>
            {(lead.contact_name || lead.title) && <p className="lead-who">{lead.contact_name}{lead.title && ` · ${lead.title}`}</p>}
            <div className="lead-pills"><span className={`pill ${lead.band}`}>{lead.score} · {lead.band}</span> <span className="pill" data-testid="drawer-stage">{stageLabel(lead.stage)}</span></div>
          </div>
          <div className="lead-nav">
            {list.length > 1 && at >= 0 && (
              <>
                <button className="btn ghost" disabled={!prevId} onClick={() => setCurrent(prevId)} aria-label="Previous lead (K)" title="Previous lead (K)">‹</button>
                <small className="lead-count" aria-live="polite">{at + 1} / {list.length}</small>
                <button className="btn ghost" disabled={!nextId} onClick={() => setCurrent(nextId)} aria-label="Next lead (J)" title="Next lead (J)">›</button>
              </>
            )}
            <Button variant="secondary" onClick={onClose}>Close</Button>
          </div>
        </div>
        {quick.length > 0 && <div className="lead-quick" aria-label="Quick actions">{quick}</div>}
        <div className="tabs scroll" role="tablist" aria-label="Lead sections">
          {TABS.map(([tid, label]) => (
            <button key={tid} role="tab" aria-selected={tab === tid} className={tab === tid ? "on" : ""} onClick={() => setTab(tid)}>{label}</button>
          ))}
        </div>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="drawer-body" role="tabpanel" aria-label={TABS.find(([tid]) => tid === tab)?.[1]}>{body}</div>
    </>
  );
}
