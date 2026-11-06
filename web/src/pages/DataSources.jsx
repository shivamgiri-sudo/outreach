import { useEffect, useState } from "react";
import { getToken } from "../api.js";
import { istLabel } from "../time.js";
import { GuideButton } from "../GuidePanel.jsx";

const DEFAULT_SAMPLE = 25;
const MAX_SAMPLE = 100;

function errorText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join("; ");
  return "";
}

async function call(path, body) {
  const token = getToken();
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(errorText(detail.detail) || `Request failed (${res.status})`);
  }
  return res.json();
}

const sources = {
  providers: () => call("/api/providers"),
  runs: () => call("/api/providers/runs"),
  compare: (providers, sample_size) => call("/api/providers/compare", { providers, sample_size }),
  apply: (run_id, provider) => call("/api/providers/apply", { run_id, provider }),
  importScraper: (json) => call("/api/providers/import-contact-scraper", { json }),
};

const when = (iso) => istLabel(iso);

function Alert({ text }) {
  return text ? <p role="alert" style={{ color: "var(--hot)", margin: "8px 0 0" }}>{text}</p> : null;
}

function ProviderList({ list, go }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Providers</h3>
      {list.map((p) => (
        <div className="row" key={p.id} style={{ justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
          <span style={{ flex: 1, minWidth: 220 }}>
            <b>{p.title}</b><br /><small className="sub">{p.configured ? p.cost_note : p.reason}</small>
          </span>
          <span className="row">
            <span className={`pill ${p.configured ? "ok" : "warn"}`}>{p.configured ? "Connected" : "Not set up"}</span>
            {p.setup_id && <GuideButton keys={`integration:${p.setup_id}`} label="How do I get this?" testId={`source-guide-${p.id}`} />}
            {!p.configured && <button className="btn ghost" onClick={() => go("setup")}>Set up in Setup</button>}
          </span>
        </div>
      ))}
    </div>
  );
}

function CompareForm({ list, onDone }) {
  const [picked, setPicked] = useState(() => list.filter((p) => p.configured).map((p) => p.id));
  const [size, setSize] = useState(DEFAULT_SAMPLE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toggle = (id) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  const run = async () => {
    setBusy(true); setError("");
    try { onDone(await sources.compare(picked, Number(size))); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Compare</h3>
      <p className="sub" style={{ margin: "0 0 8px" }}>Each ticked provider looks up the same leads. Nothing is saved to your leads until you choose.</p>
      {list.map((p) => (
        <label key={p.id} className="row" style={{ fontWeight: 400, margin: "6px 0" }}>
          <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
          {p.title}{!p.configured && <small className="sub" style={{ margin: 0 }}> (not set up, will be skipped)</small>}
        </label>
      ))}
      <label htmlFor="sample-size">How many leads to test</label>
      <input id="sample-size" type="number" min="1" max={MAX_SAMPLE} value={size} onChange={(e) => setSize(e.target.value)} style={{ maxWidth: 140 }} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" disabled={busy || picked.length === 0 || !(size >= 1 && size <= MAX_SAMPLE)} onClick={run}>
          {busy ? "Running… this can take a few minutes" : "Run comparison"}
        </button>
      </div>
      <Alert text={error} />
    </div>
  );
}

function ResultRow({ m, run, onApplied }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  if (m.skipped) {
    return <tr><td><b>{m.title}</b></td><td colSpan={9}><span className="pill warn">Skipped</span> {m.reason}</td></tr>;
  }
  const applied = (run.applied || []).includes(m.provider);
  const use = async () => {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await sources.apply(run.id, m.provider);
      setNote(`Added ${r.filled_emails} emails and ${r.filled_phones} phones.`);
      onApplied();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <tr>
      <td><b>{m.title}</b>{m.stopped && <><br /><small style={{ color: "var(--hot)" }}>Stopped: {m.stopped}</small></>}</td>
      <td>{m.leads_checked}</td>
      <td>{m.with_email}</td>
      <td>{m.with_phone}</td>
      <td>{m.coverage_pct}%</td>
      <td>{m.credits_used}</td>
      <td>{m.credits_used === 0 ? "Free" : m.cost_per_contact}</td>
      <td>{m.bounced ?? 0}</td>
      <td title={(m.error_messages || []).join("\n")}>{m.errors}</td>
      <td>
        <button className="btn ghost" disabled={busy || applied} onClick={use}>
          {applied ? "Used" : busy ? "Saving…" : "Use these results"}
        </button>
        {note && <><br /><small style={{ color: "var(--ok)" }}>{note}</small></>}
        <Alert text={error} />
      </td>
    </tr>
  );
}

function Results({ run, onApplied }) {
  if (!run) return null;
  return (
    <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 16 }}>
      <h3 style={{ margin: 16 }}>Results for {run.sample_size} leads <small className="sub">({when(run.at)})</small></h3>
      <table>
        <thead><tr>
          <th>Provider</th><th>Checked</th><th>Found email</th><th>Found phone</th><th>Coverage %</th>
          <th>Credits</th><th>Cost per contact</th><th>Bounced</th><th>Errors</th><th />
        </tr></thead>
        <tbody>{run.providers.map((m) => <ResultRow key={`${run.id}-${m.provider}`}m={m} run={run} onApplied={onApplied} />)}</tbody>
      </table>
      <p className="sub" style={{ margin: 16 }}>"Use these results" only fills emails and phones that are empty, and skips unsubscribed or disqualified leads. Bounced grows once sending starts.</p>
    </div>
  );
}

function ImportCard() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const readFile = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try { setText(await file.text()); setError(""); }
    catch { setError("Could not read that file."); }
  };

  const send = async () => {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await sources.importScraper(text)); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Import from the website contact scraper</h3>
      <p className="sub" style={{ margin: "0 0 8px" }}>Run the open-source scraper on your computer (homepage or key pages only, no proxies), then paste or upload its JSON.</p>
      <label htmlFor="scraper-file">JSON file</label>
      <input id="scraper-file" type="file" accept=".json,application/json" onChange={readFile} />
      <label htmlFor="scraper-json">Or paste the JSON</label>
      <textarea id="scraper-json" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='[{"domain": "example.in", "emails": [...], "phones": [...]}]' />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" disabled={busy || !text.trim()} onClick={send}>{busy ? "Importing…" : "Import"}</button>
      </div>
      <Alert text={error} />
      {result && (
        <div className="tip" style={{ marginTop: 12 }}>
          Matched <b>{result.matched}</b> leads. Added <b>{result.filled_emails}</b> emails and <b>{result.filled_phones}</b> phones.
          {result.unmatched_domains.length > 0 && <><br /><small>No lead for: {result.unmatched_domains.join(", ")}</small></>}
        </div>
      )}
    </div>
  );
}

function History({ runs, current, onShow }) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0 }}>Past comparisons</h3>
      {runs.length === 0 && <p className="sub" style={{ margin: 0 }}>No comparisons yet.</p>}
      {runs.map((r) => (
        <div className="row" key={r.id} style={{ justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
          <span style={{ flex: 1, minWidth: 220 }}>
            <b>{when(r.at)}</b> · {r.sample_size} leads<br />
            <small className="sub">
              {r.providers.map((m) => (m.skipped ? `${m.title}: skipped` : `${m.title}: ${m.coverage_pct}%`)).join(" · ")}
            </small>
          </span>
          <button className="btn ghost" disabled={current && current.id === r.id} onClick={() => onShow(r)}>
            {current && current.id === r.id ? "Showing" : "Show"}
          </button>
        </div>
      ))}
    </div>
  );
}

export default function DataSources({ go }) {
  const [list, setList] = useState(null);
  const [runs, setRuns] = useState([]);
  const [current, setCurrent] = useState(null);
  const [error, setError] = useState("");

  const loadRuns = async (keepId) => {
    const latest = await sources.runs();
    setRuns(latest);
    setCurrent(latest.find((r) => r.id === keepId) || latest[0] || null);
  };

  useEffect(() => {
    Promise.all([sources.providers(), sources.runs()])
      .then(([p, r]) => { setList(p); setRuns(r); setCurrent(r[0] || null); })
      .catch((e) => setError(e.message));
  }, []);

  const refresh = (keepId) => loadRuns(keepId).catch((e) => setError(e.message));

  if (error && !list) return <><h2>Data sources</h2><Alert text={error} /></>;
  if (!list) return <p className="empty">Loading…</p>;

  return (
    <>
      <h2>Data sources</h2>
      <p className="sub">Try free plans side by side on the same Indian leads, then keep the best. Only public business contacts are used, and every value keeps where it came from.</p>
      <Alert text={error} />
      <div className="grid g2">
        <ProviderList list={list} go={go} />
        <CompareForm list={list} onDone={(run) => { setCurrent(run); refresh(run.id); }} />
      </div>
      <Results run={current} onApplied={() => current && refresh(current.id)} />
      <div className="grid g2" style={{ marginTop: 16 }}>
        <ImportCard />
      </div>
      <History runs={runs} current={current} onShow={setCurrent} />
    </>
  );
}
