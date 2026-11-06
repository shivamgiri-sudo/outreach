import { useEffect, useState } from "react";
import { api } from "../api.js";

const SIGNALS = [
  ["hiring_volume", "Open roles (log scale)"], ["hiring_pain", "Hard-to-fill roles (old / reposted)"], ["fit", "Size and India fit"],
  ["engagement", "Opens, clicks, replies"], ["intent", "Said yes to contact"],
];

// Owner only: how the 0-100 lead score is weighted and where warm / hot start. Every lead rescores at once.
export default function ScoringCard() {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api.scoring().then(setCfg).catch((e) => setError(e.message)); }, []);
  if (!cfg) return error ? <div className="card tip error" role="alert">{error}</div> : null;

  const total = Object.values(cfg.weights).reduce((a, b) => a + Number(b || 0), 0);
  const setWeight = (k, v) => setCfg({ ...cfg, weights: { ...cfg.weights, [k]: v } });
  const save = async (body) => {
    setMsg(""); setError("");
    try {
      const r = await api.saveScoring(body);
      setCfg(r); setMsg("Saved. Every lead is scored with these numbers now.");
    } catch (e) { setError(e.message); }
  };
  const num = (v) => (v === "" ? "" : Number(v));
  const submit = (e) => {
    e.preventDefault();
    save({ weights: Object.fromEntries(Object.entries(cfg.weights).map(([k, v]) => [k, num(v)])), hot: num(cfg.hot), warm: num(cfg.warm),
      open_roles_full: num(cfg.open_roles_full), fit_size_min: num(cfg.fit_size_min) });
  };

  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="scoring-card">
      <b>Lead scoring</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>
        Points for each buying signal (the score is capped at 100). Open roles count on a log scale, so 80 roles beat 10; there is no upper company-size limit.
      </p>
      <form className="inline-form" onSubmit={submit}>
        <div className="split-2">
          {SIGNALS.map(([k, label]) => (
            <label key={k}>{label}
              <input type="number" min={0} max={100} required value={cfg.weights[k]} onChange={(e) => setWeight(k, e.target.value)} />
            </label>
          ))}
          <p className="sub" style={{ margin: 0, alignSelf: "end" }}>Total: <b>{total}</b> points{total > 100 ? " (scores above 100 are capped)" : ""}</p>
        </div>
        <div className="split-2">
          <label>Warm from<input type="number" min={0} max={99} required value={cfg.warm} onChange={(e) => setCfg({ ...cfg, warm: e.target.value })} /></label>
          <label>Hot from<input type="number" min={1} max={100} required value={cfg.hot} onChange={(e) => setCfg({ ...cfg, hot: e.target.value })} /></label>
          <label>Open roles for full marks<input type="number" min={2} required value={cfg.open_roles_full} onChange={(e) => setCfg({ ...cfg, open_roles_full: e.target.value })} /></label>
          <label>Smallest company size that fits<input type="number" min={0} required value={cfg.fit_size_min} onChange={(e) => setCfg({ ...cfg, fit_size_min: e.target.value })} /></label>
        </div>
        <div className="row">
          <button className="btn" type="submit">Save scoring</button>
          <button className="btn ghost" type="button" onClick={() => save(cfg.defaults)}>Back to defaults</button>
        </div>
      </form>
      {msg && <p className="sub" role="status">{msg}</p>}
      {error && <p className="tip error" role="alert">{error}</p>}
    </div>
  );
}
