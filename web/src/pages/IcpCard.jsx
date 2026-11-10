import { useEffect, useState } from "react";
import { api } from "../api.js";
import Abbr from "../components/Abbr.jsx";

export default function IcpCard() {
  const [icp, setIcp] = useState(null);
  const [text, setText] = useState("");
  const [min, setMin] = useState(40);
  const [gate, setGate] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const apply = (d) => { setIcp(d); setText(d.text || ""); setMin(d.min_score); setGate(!!d.gate); };
  useEffect(() => { api.icp().then(apply).catch((e) => setMsg(e.message)); }, []);

  const save = async () => {
    setBusy(true); setMsg("");
    try { apply(await api.saveIcp({ text, min_score: Number(min), gate })); setMsg("Saved."); }
    catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const score = async () => {
    setBusy(true); setMsg("");
    try {
      await api.saveIcp({ text, min_score: Number(min), gate });
      const r = await api.scoreIcp({ limit: 30 });
      setMsg(`Scored ${r.scored} lead(s) with the AI${r.already_scored ? `, ${r.already_scored} already done` : ""}${r.failed ? `, ${r.failed} could not be scored` : ""}.`);
      apply(await api.icp());
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  if (!icp) return msg ? <p className="empty" role="alert">{msg}</p> : null;
  const p = icp.preview || {};
  return (
    <section className="card" style={{ marginBottom: 14 }} aria-label="Ideal customer">
      <h3 style={{ marginTop: 0 }}>Ideal customer gate (<Abbr term="ICP">ICP</Abbr>)</h3>
      <p className="sub">Describe who you want in plain words. The AI scores each lead 0–100 against it (and says why). With the gate on, leads below your lowest score are held back from e-mail sequences, not deleted. Without AI scores the gate uses each lead's own score.</p>
      <label htmlFor="icp-text">Who is your ideal customer?
        <textarea id="icp-text" rows={3} maxLength={1500} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="BPO and call-centre companies in India with 100+ staff that hire in bulk and have several roles open for 30+ days." />
      </label>
      <div className="row" style={{ marginTop: 8 }}>
        <label htmlFor="icp-min">Lowest score allowed
          <input id="icp-min" type="number" min="0" max="100" value={min} onChange={(e) => setMin(e.target.value)} style={{ width: 90 }} /></label>
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={gate} onChange={(e) => setGate(e.target.checked)} /> Hold back leads below it</label>
        <button className="btn" disabled={busy} onClick={save}>Save</button>
        <button className="btn ghost" disabled={busy || !text.trim()} onClick={score}>Score 30 leads with AI</button>
      </div>
      <p className="sub" style={{ marginBottom: 0 }}>
        At {icp.min_score}: {p.would_pass ?? 0} lead(s) pass, {p.held_back ?? 0} would be held back{p.gate_on ? " (gate is on)" : " (gate is off)"}.
        {(p.examples || []).length > 0 && <> For example {p.examples.map((x) => `${x.company} (${x.score})`).join(", ")}.</>}
      </p>
      {msg && <p role="status" className="sub">{msg}</p>}
    </section>
  );
}
