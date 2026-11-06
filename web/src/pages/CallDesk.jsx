import { useEffect, useState } from "react";
import { api } from "../api.js";
import LeadDrawer from "./LeadDrawer.jsx";

const OUTCOMES = ["Connected – interested", "Call back later", "Not interested", "Wrong number", "No answer"];
const PD_NOTE = "Calls are placed by a person, never by a bot or an auto-dialler. Redial missed leads the same day, before 18:55.";
const SCRIPT = [
  ["Opener", "Hi {{first_name}}, {{rep}} from BookMyInterview. You replied about screening for {{role}} — is this a good minute?"],
  ["Opener (Hinglish)", "Hi {{first_name}}, main {{rep}}, BookMyInterview se. Aapne {{role}} ki screening ke baare mein reply kiya tha — kya abhi ek minute baat ho sakti hai?"],
  ["Ask", "How is hiring for {{role}} going right now — what takes most of your team's time?"],
  ["Proof", "Honestly, we're in beta and have no case studies yet. That's why your first role is free, so you can judge it on your own candidates."],
  ["Next step", "Shall I set up your first role with you now? It's free and takes a few minutes."],
];
const OBJECTIONS = [
  ["We use Naukri / another portal", "Keep it. This starts after applications arrive: everyone is screened once and you only meet the best few."],
  ["No time", "Understood. When is a better time? I'll call back then."],
  ["Send details", "Of course. I'll send the sample screen for your role. WhatsApp or email?"],
  ["What does it cost after the free period?", "I don't want to guess on the call. I'll check the current plans and send them in writing. Nothing is charged unless you choose to continue."],
  ["Is candidate data safe?", "It's used only for your hiring and deleted on request, in line with the DPDP Act."],
  ["We have our own HR team / ATS", "Good, keep them. This only does the first screen and the booking, so your team spends time on the right people."],
  ["AI can't judge people", "Agreed. The AI ranks, you decide, and every score comes with a reason you can check."],
  ["How did you get my number?", "From your public job post or company website. If you'd rather not hear from us, I'll delete it right now."],
  ["Candidates won't do voice answers", "Fair worry. It's one short answer from their phone. The free role is the easiest way to see if your candidates do it."],
  ["We're not hiring now", "No problem. Shall I check back in about 2 months?"],
];

export default function CallDesk() {
  const [leads, setLeads] = useState([]);
  const [open, setOpen] = useState(null);
  const [dialUrl, setDialUrl] = useState("");
  const [rep, setRep] = useState("your name");
  const [forLead, setForLead] = useState("");
  useEffect(() => { api.me().then((m) => setRep(m.email ? m.name : "your name")).catch(() => {}); }, []);
  useEffect(() => {
    api.integrations().then((list) => {
      const t = list.find((i) => i.id === "telephony");
      setDialUrl(t && t.mode === "api" ? t.values.click_to_call_url || "" : "");
    }).catch(() => {});
  }, []);
  const dialLink = (phone) => (dialUrl.includes("{phone}") ? dialUrl.replace("{phone}", encodeURIComponent(phone)) : `tel:${phone}`);

  const load = () => api.leads().then((all) => setLeads(all.filter((l) => ["interested", "whatsapp_active"].includes(l.stage))));
  useEffect(() => { load(); }, []);

  const log = async (id, outcome) => { await api.note(id, `Call outcome: ${outcome}`, "call"); load(); };

  return (
    <>
      <h2>Call desk</h2>
      <p className="sub">Leads who said yes. Call hot ones first.</p>
      <p className="sub"><b>{PD_NOTE}</b></p>
      <div className="grid g2">
        <div className="card" style={{ padding: 0 }}>
          {leads.length === 0 ? <p className="empty">No one is waiting for a call yet.</p> : (
            <table>
              <thead><tr><th>Lead</th><th>Phone</th><th>Outcome</th></tr></thead>
              <tbody>
                {leads.map((l) => (
                  <tr key={l.id}>
                    <td><a href="#" onClick={(e) => { e.preventDefault(); setOpen(l.id); }}><b>{l.company}</b></a><br /><small>{l.contact_name} · score {l.score}</small></td>
                    <td>{l.phone ? <a href={dialLink(l.phone)}>{l.phone}</a> : "—"}</td>
                    <td>
                      <select defaultValue="" onChange={(e) => e.target.value && log(l.id, e.target.value)}>
                        <option value="" disabled>Log outcome…</option>
                        {OUTCOMES.map((o) => <option key={o}>{o}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Call script</h3>
          {leads.length > 0 && <label>Script for <select value={forLead} onChange={(e) => setForLead(e.target.value)}>
            {leads.map((l) => <option key={l.id} value={l.id}>{l.contact_name || l.company} · {l.company}</option>)}</select></label>}
          {(() => {
            const l = leads.find((x) => x.id === forLead) || leads[0] || {};
            const first = (l.contact_name || "").trim().split(/\s+/)[0] || "there";
            const fill = (text) => text.replaceAll("{{rep}}", rep).replaceAll("{{first_name}}", first)
              .replaceAll("{{role}}", l.role || "your open roles").replaceAll("{{company}}", l.company || "your company");
            return SCRIPT.map(([t, text]) => <p key={t}><b>{t}:</b> {fill(text)}</p>);
          })()}
          <h3>If they say…</h3>
          {OBJECTIONS.map(([t, text]) => <p key={t}><b>“{t}”</b> — {text}</p>)}
        </div>
      </div>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
