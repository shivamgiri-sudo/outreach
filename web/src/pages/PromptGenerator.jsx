import { useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";

const START = { use_ai: true, channel: "email", industry: "", role: "", tone: "warm", language: "English", goal: "", story: "", dos: "", donts: "" };

export default function PromptGenerator() {
  const [spec, setSpec] = useState(START);
  const [prompt, setPrompt] = useState("");
  const [copied, setCopied] = useState(false);
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setSpec({ ...spec, [k]: v });

  const generate = async () => {
    setBusy(true); setInfo("");
    try {
      const r = await api.generatePrompt(spec);
      setPrompt(r.prompt);
      setInfo(r.ai ? "Written with Claude. The honesty rules below were added by the app." : (r.note || ""));
    } catch (e) { setInfo(e.message); }
    setBusy(false);
  };
  const copy = async () => {
    setCopied(await copyText(prompt));
    setTimeout(() => setCopied(false), 1500);
  };
  const Select = ({ k, options }) => (
    <select value={spec[k]} onChange={(e) => set(k, e.target.value)}>{options.map((o) => <option key={o}>{o}</option>)}</select>
  );

  return (
    <>
      <h2>Prompt generator</h2>
      <p className="sub">Build a ready system prompt for any industry, role and channel. The honesty rules are always added last and override everything above them, including your Do and Don't lines: no invented numbers, customers or stories, WhatsApp only after a yes, calls through PD only before 18:55, and an opt-out in every message.</p>
      <div className="grid g2">
        <div className="card">
          <div className="row">
            <div style={{ flex: 1 }}><label>Channel</label><Select k="channel" options={["email", "whatsapp", "voice"]} /></div>
            <div style={{ flex: 1 }}><label>Language</label><Select k="language" options={["English", "Hindi", "Hinglish"]} /></div>
            <div style={{ flex: 1 }}><label>Tone</label><Select k="tone" options={["warm", "friendly", "formal", "playful"]} /></div>
          </div>
          <label>Industry</label><input value={spec.industry} onChange={(e) => set("industry", e.target.value)} placeholder="BPO, retail, logistics…" />
          <label>Role being hired</label><input value={spec.role} onChange={(e) => set("role", e.target.value)} placeholder="Telecaller, accountant…" />
          <label>Goal</label><input value={spec.goal} onChange={(e) => set("goal", e.target.value)} placeholder="Offer a free sample screen for their role" />
          <label>Facts or a customer story you may use (optional, only with permission)</label><textarea rows={2} value={spec.story} onChange={(e) => set("story", e.target.value)} />
          <label>Do (preferences; cannot relax the honesty rules)</label><input value={spec.dos} onChange={(e) => set("dos", e.target.value)} />
          <label>Don't</label><input value={spec.donts} onChange={(e) => set("donts", e.target.value)} />
          <label className="row" style={{ fontWeight: 400, marginTop: 10 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={!!spec.use_ai} onChange={(e) => set("use_ai", e.target.checked)} />
            Write it with Claude (richer wording; the honesty rules are still added by the app)
          </label>
          <button className="btn" style={{ marginTop: 12 }} onClick={generate} disabled={busy}>{busy ? "Writing…" : "Generate prompt"}</button>
        </div>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>Your prompt</h3>
            {prompt && <button className="btn ghost" onClick={copy}>{copied ? "Copied" : "Copy"}</button>}
          </div>
          {info && <p className="sub" role="status">{info}</p>}
          {prompt ? <pre>{prompt}</pre> : <p className="empty">Fill the form and click Generate.</p>}
        </div>
      </div>
    </>
  );
}
