// Lead ageing on the Leads page: the Ageing filter, the Aged pill, "Make a re-engage list", the owner's day rules,
// and the read-only "Find duplicates" report. Nothing here changes a stage, stops a sequence or sends a message.
import { useEffect, useState } from "react";
import { api, canWrite, isManager, isOwner } from "../api.js";
import { Modal } from "./setup/common.jsx";

const DEFAULTS = { untouched_days: 7, stale_days: 14, stuck_days: 21 };
const KINDS = (d) => [["untouched", `Untouched ${d.untouched_days}+ days`], ["stale", `Gone quiet ${d.stale_days}+ days`], ["stuck", `Stuck in stage ${d.stuck_days}+ days`]];
const WHY = (d) => ({ untouched: `Never contacted, ${d.untouched_days}+ days old`, stale: `Last attempt ${d.stale_days}+ days ago and no reply`, stuck: `Same stage for ${d.stuck_days}+ days` });

export function useAgeingRules() {
  const [rules, setRules] = useState(DEFAULTS);
  useEffect(() => { api.leadAgeing().then((r) => setRules({ ...DEFAULTS, ...r })).catch(() => {}); }, []);
  return [rules, setRules];
}

export function AgedPill({ lead, rules }) {
  if (!lead.ageing) return null;
  return <span className="pill warn" style={{ marginLeft: 6 }} data-testid="aged-pill" title={WHY(rules)[lead.ageing]}>Aged</span>;
}

export function AgeingFilter({ leads, value, onChange, rules }) {
  return (
    <select aria-label="Filter by ageing" value={value} onChange={(e) => onChange(e.target.value)} data-testid="ageing-filter">
      <option value="">Any ageing</option>
      {KINDS(rules).map(([k, v]) => <option key={k} value={k}>{v} ({leads.filter((l) => l.ageing === k).length})</option>)}
    </select>
  );
}

function Rules({ rules, onSaved }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(rules);
  const [err, setErr] = useState("");
  useEffect(() => { setDraft(rules); }, [rules]);
  const save = () => api.saveLeadAgeing(draft).then((r) => { onSaved({ ...DEFAULTS, ...r }); setOpen(false); setErr(""); }).catch((e) => setErr(e.message));
  return (
    <>
      <button type="button" className="btn ghost" onClick={() => setOpen(true)} data-testid="ageing-rules">Ageing days</button>
      {open && (
        <Modal title="When is a lead aged?" description="Open leads only. Closed leads, marked invalid or consultant leads and do-not-contact people are never aged." onClose={() => setOpen(false)} testId="ageing-rules-modal">
          {[["untouched_days", "Never contacted for this many days"], ["stale_days", "Last attempt this many days ago, with no reply"], ["stuck_days", "Same stage for this many days"]].map(([k, label]) => (
            <label key={k}>{label}<input type="number" min="1" max="3650" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></label>
          ))}
          {err && <div className="tip error" role="alert">{err}</div>}
          <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button><button className="btn" onClick={save}>Save</button>
          </div>
        </Modal>
      )}
    </>
  );
}

export function ReEngageButton({ shown, kind, onNotice, onDone }) {
  const [busy, setBusy] = useState(false);
  const aged = shown.filter((l) => l.ageing && (!kind || l.ageing === kind));
  if (!canWrite() || !aged.length) return null;
  const make = async () => {
    setBusy(true);
    try { const r = await api.reEngage(aged.map((l) => l.id), kind); onNotice(r.notice); onDone?.(); } catch (e) { onNotice(e.message); }
    setBusy(false);
  };
  return (
    <button type="button" className="btn ghost" disabled={busy} onClick={make} data-testid="re-engage"
      title="Saves these aged leads as a list. It changes no stage, stops nothing and sends nothing.">
      {busy ? "Making the list…" : `Make a re-engage list (${aged.length})`}
    </button>
  );
}

function DuplicatesReport({ onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.leadDuplicates().then(setData).catch((e) => setErr(e.message)); }, []);
  return (
    <Modal title="Duplicate leads" description="Leads that share an email, a phone number or a LinkedIn profile. This is only a report: nothing is merged or changed." onClose={onClose} wide testId="duplicates-report">
      {err && <div className="tip error" role="alert">{err}</div>}
      {!data && !err && <p className="sub">Looking…</p>}
      {data && data.groups.length === 0 && <p>No duplicates found.</p>}
      {data && data.groups.length > 0 && (
        <>
          <p className="sub">{data.total_groups} group{data.total_groups === 1 ? "" : "s"}{data.shown < data.total_groups ? `, showing the first ${data.shown}` : ""}.</p>
          {data.groups.map((g, i) => (
            <div key={i} className="card" style={{ marginBottom: 8 }}>
              <small>{g.reasons.join(", ")} · {g.size} leads</small>
              {g.leads.map((l) => (
                <p key={l.id} style={{ margin: "4px 0" }}><a href={`#leads?lead=${l.id}`} onClick={onClose}><b>{l.company || l.contact_name || l.id}</b></a> {l.contact_name} {l.email || l.phone} <small>({l.stage}{l.owner ? `, ${l.owner}` : ""})</small></p>
              ))}
            </div>
          ))}
        </>
      )}
      <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}><button className="btn" onClick={onClose}>Close</button></div>
    </Modal>
  );
}

export function FindDuplicates() {
  const [open, setOpen] = useState(false);
  if (!isManager()) return null;
  return (<><button type="button" className="btn ghost" onClick={() => setOpen(true)} data-testid="find-duplicates">Find duplicates</button>{open && <DuplicatesReport onClose={() => setOpen(false)} />}</>);
}

export function AgeingTools({ shown, kind, rules, setRules, onNotice, onDone }) {
  return (
    <>
      <ReEngageButton shown={shown} kind={kind} onNotice={onNotice} onDone={onDone} />
      {isOwner() && <Rules rules={rules} onSaved={setRules} />}
      <FindDuplicates />
    </>
  );
}
