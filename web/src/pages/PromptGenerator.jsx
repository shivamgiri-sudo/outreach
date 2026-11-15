import { useState } from "react";
import { api, canWrite } from "../api.js";
import { copyText } from "../clipboard.js";
import { PageHeader, Card, CardHeader, Field, Input, Button, Switch, EmptyState, Skeleton } from "../ui/index.js";
import "../work-pages.css";

const START = { use_ai: true, channel: "email", industry: "", role: "", tone: "warm", language: "English", goal: "", story: "", dos: "", donts: "" };

function Pick({ spec, set, k, options }) {
  return <select value={spec[k]} onChange={(e) => set(k, e.target.value)}>{options.map((o) => <option key={o}>{o}</option>)}</select>;
}

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

  return (
    <>
      <PageHeader title="Prompt generator"
        description={<>Build a ready system prompt for any industry, role and channel. The honesty rules are always added last and override everything above them, including your Do and Don't lines: no invented numbers, customers or stories, WhatsApp only after a yes, calls through PD only before 18:55, and an opt-out in every message.</>} />
      <div className="grid g2">
        <Card>
          <CardHeader title="What to write" />
          <div className="stack">
            <div className="form-grid">
              <Field label="Channel"><Pick spec={spec} set={set} k="channel" options={["email", "whatsapp", "voice"]} /></Field>
              <Field label="Language"><Pick spec={spec} set={set} k="language" options={["English", "Hindi", "Hinglish"]} /></Field>
              <Field label="Tone"><Pick spec={spec} set={set} k="tone" options={["warm", "friendly", "formal", "playful"]} /></Field>
            </div>
            <Field label="Industry"><Input value={spec.industry} onChange={(e) => set("industry", e.target.value)} placeholder="BPO, retail, logistics…" /></Field>
            <Field label="Role being hired"><Input value={spec.role} onChange={(e) => set("role", e.target.value)} placeholder="Telecaller, accountant…" /></Field>
            <Field label="Goal"><Input value={spec.goal} onChange={(e) => set("goal", e.target.value)} placeholder="Offer a free sample screen for their role" /></Field>
            <Field label="Facts or a customer story you may use (optional, only with permission)"><textarea rows={2} value={spec.story} onChange={(e) => set("story", e.target.value)} /></Field>
            <Field label="Do (preferences; cannot relax the honesty rules)"><Input value={spec.dos} onChange={(e) => set("dos", e.target.value)} /></Field>
            <Field label="Don't"><Input value={spec.donts} onChange={(e) => set("donts", e.target.value)} /></Field>
            <div className="row">
              <Switch checked={!!spec.use_ai} onChange={(v) => set("use_ai", v)} label="Write it with Claude" />
              <span className="sub-inline">Write it with Claude (richer wording; the honesty rules are still added by the app)</span>
            </div>
            {canWrite() && <div><Button onClick={generate} disabled={busy}>{busy ? "Writing…" : "Generate prompt"}</Button></div>}
          </div>
        </Card>
        <Card>
          <CardHeader title="Your prompt" actions={prompt ? <Button variant="secondary" onClick={copy}>{copied ? "Copied" : "Copy"}</Button> : null} />
          {info && <p className="sub" role="status">{info}</p>}
          {busy && !prompt && <div className="stack" aria-busy="true"><Skeleton height={16} /><Skeleton height={16} /><Skeleton height={16} width="70%" /></div>}
          {prompt ? <pre>{prompt}</pre> : !busy && <EmptyState icon="wand" title="Nothing written yet">Fill the form on the left and press Generate prompt.</EmptyState>}
        </Card>
      </div>
    </>
  );
}
