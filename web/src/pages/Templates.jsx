import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { copyText } from "../clipboard.js";
import PreviewDrawer from "./sequences/PreviewDrawer.jsx";
import SequenceEditor from "./sequences/SequenceEditor.jsx";
import Snippets from "./sequences/Snippets.jsx";
import { humanDay, stepTitle } from "./sequences/lint.js";

const UNSUB = "\n\nPrefer no more emails? {{unsub_link}}";
const email = (text) => `{{greeting}}\n\n${text}\n\n— {{sender}}${UNSUB}`;
const OTHERS = [
  { channel: "LinkedIn (send by hand)", name: "Comment on a job post", body: "Hiring for {{role}} usually means a lot of CV reading. One thing that helps: ask every applicant the same 3 short questions before any call, then compare the answers side by side." },
  { channel: "LinkedIn (send by hand)", name: "Connection note", body: "Hi {{first_name}}, I'm {{sender}} from BookMyInterview. We're building a simpler way to screen high-volume hiring (in beta). Saw you're hiring for {{role}}, happy to connect." },
  { channel: "Email", name: "Help with existing applicants", body: email("If you already have applicants for {{role}}, I can help you add them to your free first role, so you see how the screen ranks your own candidates.") },
  { channel: "Email", name: "Honest heads-up (free allowance nearly used)", body: email("A quick heads-up: you're close to the free limit on your first role. As a reminder: {{offer_line}}\n\nI'd rather you hear it from me early. If it has been useful, reply and I'll send what continuing costs. If not, no problem.") },
  { channel: "Email", name: "After call recap", body: email("Thanks for your time today. Recap: [3 lines from the call].\n\nHere is the sample screen for your role: {{demo_link}}") },
  { channel: "Email", name: "Referral ask", body: email("Glad it helped. If you know another team that is hiring, I'd be happy to give them the same free first role.") },
  { channel: "Email", name: "Testimonial request", body: email("Glad the role went well. Would you share one line on what changed? I'll only use it with your OK.") },
];
const WA_NAMES = {
  first_after_yes: "First message (after yes)",
  nudge_demo_not_opened: "Nudge: demo not opened",
  nudge_not_published: "Nudge: drafted but not published",
  pilot_offer: "Offer: help with existing applicants",
};
const waName = (key) => (key.endsWith("_hinglish") ? `${WA_NAMES[key.replace(/_hinglish$/, "")] || key} (Hinglish)` : WA_NAMES[key] || key);
const TABS = [["sequences", "Email sequences"], ["snippets", "Snippet library"], ["other", "WhatsApp & other messages"]];

function CopyCard({ t, copied, onCopy }) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <b>{t.name} <span className="pill">{t.channel}</span></b>
        <button className="btn ghost" onClick={() => onCopy(t.name, (t.subject ? `Subject: ${t.subject}\n\n` : "") + t.body)}>{copied === t.name ? "Copied" : "Copy"}</button>
      </div>
      {t.subject && <p><b>Subject:</b> {t.subject}</p>}
      <pre>{t.body}</pre>
    </div>
  );
}

function SequenceCard({ s, onOpen, onDuplicate, onArchive, onPreview }) {
  const tests = s.steps.filter((x) => (x.variants || []).length).length;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <b>{s.name}</b> {s.builtin && <span className="pill">Built in</span>} {s.archived && <span className="pill warn">Archived</span>}
          {tests > 0 && <span className="pill rec" style={{ marginLeft: 4 }}>A/B on {tests} step{tests > 1 ? "s" : ""}</span>}
          <p className="sub" style={{ margin: "4px 0 0" }}>{s.steps.length} emails on day {s.steps.map((x) => humanDay(x.day)).join(", ")} · {s.in_use} running now{s.notes ? ` · ${s.notes}` : ""}</p>
        </div>
        <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button className="btn ghost" onClick={() => onOpen(s)}>{s.builtin || !isOwner() ? "View" : "Edit"}</button>
          {isOwner() && <button className="btn ghost" onClick={() => onDuplicate(s)}>Duplicate</button>}
          {isOwner() && !s.builtin && <button className="btn ghost" onClick={() => onArchive(s)}>{s.archived ? "Restore" : "Archive"}</button>}
        </span>
      </div>
      <details>
        <summary>Steps</summary>
        <ol style={{ paddingLeft: 18 }}>
          {s.steps.map((x, i) => (
            <li key={i} style={{ margin: "6px 0" }}>
              <b>Day {humanDay(x.day)} · {stepTitle(x.name)}</b>{x.winner && <span className="pill ok" style={{ marginLeft: 6 }}>Winner {x.winner}</span>}
              <div className="sub">Subject: {x.subject}</div>
              <button className="btn ghost" style={{ marginTop: 4 }} onClick={() => onPreview(s, i)}>Preview designed email</button>
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

export default function Templates() {
  const [tab, setTab] = useState("sequences");
  const [list, setList] = useState([]);
  const [showArchived, setShowArchived] = useState(false);
  const [editing, setEditing] = useState(null);
  const [shown, setShown] = useState(null);
  const [whatsapp, setWhatsapp] = useState([]);
  const [copied, setCopied] = useState("");
  const [msg, setMsg] = useState("");
  const [me, setMe] = useState(null);

  const load = () => api.sequences(showArchived).then(setList).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, [showArchived]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.me().then(setMe).catch(() => setMe(null)); }, []);
  useEffect(() => {
    api.whatsappTemplates()
      .then((all) => setWhatsapp(Object.entries(all).map(([key, body]) => ({ name: waName(key), channel: "WhatsApp", body }))))
      .catch(() => setWhatsapp([]));
  }, []);

  const copy = async (name, text) => {
    setCopied((await copyText(text)) ? name : "failed");
    setTimeout(() => setCopied(""), 1500);
  };
  const preview = (s, index) => api.sequenceStepPreview(s.id, index)
    .then((r) => setShown({ ...r, name: `${s.name}: Day ${humanDay(s.steps[index].day)} · ${stepTitle(s.steps[index].name)}`, seqId: s.id, index }))
    .catch((e) => setMsg(e.message));
  const duplicate = async (s) => {
    try { const copyRow = await api.duplicateSequence(s.id); await load(); setEditing(copyRow); setMsg(`Made “${copyRow.name}”. Change it below and save.`); }
    catch (e) { setMsg(e.message); }
  };
  const archive = async (s) => {
    if (!s.archived && !window.confirm(`Archive “${s.name}”? Leads already in it carry on; it can't be chosen for new leads.`)) return;
    try { await api.archiveSequence(s.id, !s.archived); load(); } catch (e) { setMsg(e.message); }
  };
  const newSequence = () => setEditing({ name: "", notes: "", steps: [{ day: 0, name: "Email 1", subject: "", body: "{{greeting}}\n\n\n\nRegards,\n{{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}", variants: [] }] });

  return (
    <>
      <h2>Sequences & templates</h2>
      <p className="sub">Short, human, no hard sell, no invented stories. Words in {"{{double braces}}"} are filled per lead ({"{{greeting}}"}, {"{{first_name}}"}, {"{{company}}"}, {"{{role}}"}, {"{{sender}}"}, {"{{offer_line}}"}, {"{{demo_link}}"}, {"{{unsub_link}}"}); [square brackets] you fill by hand. A sequence stops on a reply. Cold emails carry no link except the unsubscribe link.</p>
      <div className="tabs scroll" role="tablist" aria-label="Template sections">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setMsg(""); }}>{label}</button>
        ))}
      </div>
      {msg && <p className="sub" role="status">{msg}</p>}
      {tab === "sequences" && (editing ? (
        <SequenceEditor key={editing.id || "new"} sequence={editing} onClose={() => { setEditing(null); load(); }}
          onSaved={(saved) => { setEditing(saved); load(); }} onDuplicate={duplicate} />
      ) : (
        <>
          <p className="sub">Each email goes out designed (logo, layout) with the same words, plus a plain-text version. Pick the sequence when you start a lead (contact panel); lists and campaigns use the default.</p>
          <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {isOwner() && <button className="btn" onClick={newSequence}>New sequence</button>}
            <label className="row" style={{ gap: 6, margin: 0 }}><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} style={{ width: "auto" }} /> Show archived</label>
          </div>
          {list.map((s) => <SequenceCard key={s.id} s={s} onOpen={setEditing} onDuplicate={duplicate} onArchive={archive} onPreview={preview} />)}
        </>
      ))}
      {tab === "snippets" && <Snippets me={me} />}
      {tab === "other" && (
        <>
          <h3>WhatsApp (only after a recorded yes)</h3>
          {whatsapp.map((t) => <CopyCard key={t.name} t={t} copied={copied} onCopy={copy} />)}
          <h3>Other messages (copy and send)</h3>
          {OTHERS.map((t) => <CopyCard key={t.name} t={t} copied={copied} onCopy={copy} />)}
        </>
      )}
      <PreviewDrawer shown={shown} onClose={() => setShown(null)} onSend={shown ? (to) => api.sendSequenceStepPreview(shown.seqId, shown.index, to) : null} />
    </>
  );
}
