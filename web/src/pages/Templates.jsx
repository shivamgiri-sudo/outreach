import { useEffect, useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";

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
const VARIANT_NAMES = {
  body_no_role: "If we don't know the role",
  body_with_link: "With sample link (A/B test)",
  body_with_link_no_role: "With sample link, no role",
};

const waName = (key) => (key.endsWith("_hinglish") ? `${WA_NAMES[key.replace(/_hinglish$/, "")] || key} (Hinglish)` : WA_NAMES[key] || key);
const variantsOf = (step) => Object.keys(VARIANT_NAMES).filter((k) => step[k]).map((k) => [VARIANT_NAMES[k], step[k]]);

export default function Templates() {
  const [sequence, setSequence] = useState([]);
  const [whatsapp, setWhatsapp] = useState([]);
  const [copied, setCopied] = useState("");
  useEffect(() => { api.sequence().then((s) => setSequence(s.map((t) => ({ ...t, channel: "Email", variants: variantsOf(t) })))); }, []);
  useEffect(() => {
    api.whatsappTemplates()
      .then((all) => setWhatsapp(Object.entries(all).map(([key, body]) => ({ name: waName(key), channel: "WhatsApp", body }))))
      .catch(() => setWhatsapp([]));
  }, []);

  const copy = async (name, text) => {
    setCopied((await copyText(text)) ? name : "failed");
    setTimeout(() => setCopied(""), 1500);
  };
  const Item = ({ t }) => (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{t.day !== undefined ? `Day ${t.day} · ` : ""}{t.name} <span className="pill">{t.channel}</span></b>
        <button className="btn ghost" onClick={() => copy(t.name, (t.subject ? `Subject: ${t.subject}\n\n` : "") + t.body)}>{copied === t.name ? "Copied" : "Copy"}</button>
      </div>
      {t.subject && <p><b>Subject:</b> {t.subject}</p>}
      <pre>{t.body}</pre>
      {(t.variants || []).map(([label, body]) => (
        <details key={label}><summary>{label}</summary><pre>{body}</pre></details>
      ))}
    </div>
  );
  return (
    <>
      <h2>Sequences & templates</h2>
      <p className="sub">Short, human, no hard sell, no invented stories. Words in {"{{double braces}}"} are filled per lead ({"{{greeting}}"}, {"{{first_name}}"}, {"{{company}}"}, {"{{role}}"}, {"{{sender}}"}, {"{{offer_line}}"}, {"{{demo_link}}"}, {"{{unsub_link}}"}); [square brackets] you fill by hand. The email sequence stops on a reply. Cold emails carry no link except the unsubscribe link.</p>
      <h3>Email sequence (4 steps, sent automatically)</h3>
      {sequence.map((t) => <Item key={t.name} t={t} />)}
      <h3>WhatsApp (only after a recorded yes)</h3>
      {whatsapp.map((t) => <Item key={t.name} t={t} />)}
      <h3>Other messages (copy and send)</h3>
      {OTHERS.map((t) => <Item key={t.name} t={t} />)}
    </>
  );
}
