// Owner tab of Prospect search: lookup spend and budget, provider cost/order, which sources may be stored, import, sync.
import { useEffect, useState } from "react";
import { prospects } from "../prospectsApi.js";

function Spend() {
  const [d, setD] = useState(null);
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState("");
  const load = () => prospects.spend().then((r) => {
    setD(r);
    setForm({ budget: r.budget ?? "", freshness: r.freshness_days, providers: Object.fromEntries(r.providers.map((p) => [p.id, { cost: p.cost, priority: p.priority, enabled: p.enabled }])) });
  }).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!d || !form) return <p className="sub">{msg || "Loading spend…"}</p>;
  const setP = (id, k, v) => setForm({ ...form, providers: { ...form.providers, [id]: { ...form.providers[id], [k]: v } } });
  const save = async () => {
    setMsg("");
    try {
      await prospects.saveSpend({ monthly_budget: form.budget === "" ? null : Number(form.budget), freshness_days: Number(form.freshness) || 90, providers: form.providers });
      setMsg("Saved."); load();
    } catch (e) { setMsg(e.message); }
  };
  return (
    <section className="card" data-testid="prospect-spend">
      <h3>Lookup spend</h3>
      <p className="sub">Emails already in the prospect database (seen within the freshness window) cost nothing. Providers run in the order below and stop at the first verified email; spend is counted only when a provider returns something usable.</p>
      <div className="pd-grid">
        <div><small className="sub">This month ({d.month})</small><div style={{ fontSize: 20, fontWeight: 700 }}>{d.spent}</div></div>
        <div><small className="sub">Monthly budget</small><div style={{ fontSize: 20, fontWeight: 700 }}>{d.budget ?? "No cap"}</div></div>
        <div><small className="sub">Left</small><div style={{ fontSize: 20, fontWeight: 700 }}>{d.left ?? "—"}</div></div>
      </div>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div style={{ flex: "1 1 160px" }}><label htmlFor="ps-budget">Monthly budget (blank = no cap)</label>
          <input id="ps-budget" inputMode="decimal" value={form.budget} onChange={(e) => setForm({ ...form, budget: e.target.value })} /></div>
        <div style={{ flex: "1 1 160px" }}><label htmlFor="ps-fresh">Reuse stored emails for (days)</label>
          <input id="ps-fresh" inputMode="numeric" value={form.freshness} onChange={(e) => setForm({ ...form, freshness: e.target.value })} /></div>
      </div>
      <div className="table-card" style={{ marginTop: 12 }}>
        <table className="ps-table">
          <thead><tr><th>Provider</th><th>On</th><th>Order</th><th>Cost / lookup</th><th>Connected</th></tr></thead>
          <tbody>
            {d.providers.map((p) => (
              <tr key={p.id}>
                <td>{p.title}<div><small className="sub">{[p.finds && "finds", p.verifies && "verifies"].filter(Boolean).join(", ")}</small></div></td>
                <td><input type="checkbox" aria-label={`Use ${p.title}`} checked={form.providers[p.id].enabled} onChange={(e) => setP(p.id, "enabled", e.target.checked)} /></td>
                <td><input aria-label={`Order of ${p.title}`} inputMode="numeric" style={{ width: 70 }} value={form.providers[p.id].priority} onChange={(e) => setP(p.id, "priority", e.target.value)} /></td>
                <td><input aria-label={`Cost of ${p.title}`} inputMode="decimal" style={{ width: 90 }} value={form.providers[p.id].cost} onChange={(e) => setP(p.id, "cost", e.target.value)} /></td>
                <td>{p.connected ? <span className="pill ok">Yes</span> : <span className="pill">No</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={save}>Save spend settings</button>{msg && <span className="sub" role="status">{msg}</span>}</div>
      <h3 style={{ marginTop: 18 }}>Monthly spend and hit rate</h3>
      {d.rows.length === 0 ? <p className="sub">No lookups yet.</p> : (
        <div className="table-card">
          <table className="ps-table" data-testid="spend-table">
            <thead><tr><th>Month</th><th>Provider</th><th>Lookups</th><th>Hits</th><th>Hit rate</th><th>From cache</th><th>Spend</th></tr></thead>
            <tbody>{d.rows.map((r) => (
              <tr key={r.month + r.provider}><td>{r.month}</td><td>{r.provider}</td><td>{r.lookups}</td><td>{r.hits}</td><td>{Math.round(r.hit_rate * 100)}%</td><td>{r.cached}</td><td>{r.spend}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Sources({ onChanged }) {
  const [s, setS] = useState(null);
  const [off, setOff] = useState([]);
  const [purge, setPurge] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { prospects.settings().then((r) => { setS(r); setOff(r.disabled_sources); }).catch((e) => setMsg(e.message)); }, []);
  if (!s) return <p className="sub">{msg || "Loading…"}</p>;
  const save = async () => {
    try { const r = await prospects.saveSettings({ disabled_sources: off, purge }); setMsg(`Saved. Switched off: ${r.disabled_sources.join(", ") || "none"}.`); onChanged?.(); } catch (e) { setMsg(e.message); }
  };
  return (
    <section className="card" data-testid="prospect-sources">
      <h3>Sources the database may keep</h3>
      <p className="sub">Values from a switched-off source are not stored and stop showing at once. Tick “remove” to delete what it already gave.</p>
      <div className="pf-checks ps-sources">
        {s.known_sources.filter((x) => x !== "manual").map((x) => (
          <label key={x} className="pf-check"><input type="checkbox" checked={!off.includes(x)} onChange={(e) => setOff(e.target.checked ? off.filter((y) => y !== x) : [...off, x])} /><span>{x}</span></label>
        ))}
      </div>
      <label className="pf-check"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /><span>Also remove values already stored from switched-off sources</span></label>
      <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save}>Save sources</button>{msg && <span className="sub" role="status">{msg}</span>}</div>
    </section>
  );
}

function OptOuts({ version, onChanged }) {
  const [rows, setRows] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const load = () => prospects.suppressions().then((r) => setRows(r.rows)).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, [version]);
  const undo = async (value) => {
    if (!window.confirm(`Allow ${value} back into searches, exports and lead adds?`)) return;
    setBusy(value); setMsg("");
    try {
      const r = await prospects.unsuppress(value);
      setMsg(`Un-suppressed ${r.unsuppressed}: ${r.people} people back` + (r.kept ? `, ${r.kept} kept suppressed (do-not-contact, unsubscribe or erased).` : "."));
      load(); onChanged?.();
    } catch (e) { setMsg(e.message); } finally { setBusy(""); }
  };
  if (!rows) return <p className="sub">{msg || "Loading opt-outs…"}</p>;
  return (
    <section className="card" data-testid="prospect-optouts">
      <h3>Opt-outs</h3>
      <p className="sub">Undo an opt-out made here. Anyone on the do-not-contact, unsubscribe or erased lists always stays suppressed.</p>
      {rows.length === 0 ? <p className="sub">No opt-outs.</p> : (
        <div className="table-card">
          <table className="ps-table">
            <thead><tr><th>Email or domain</th><th>Kind</th><th>Reason</th><th>When</th><th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.value}>
                <td>{r.value}</td><td>{r.kind}</td><td>{r.reason}</td><td>{(r.at || "").slice(0, 10)}</td>
                <td>{r.blocked ? <small className="sub" title={r.blocked}>Locked: {r.blocked}</small> : (
                  <button className="btn ghost" disabled={busy === r.value} aria-label={`Un-suppress ${r.value}`} onClick={() => undo(r.value)}>Un-suppress</button>
                )}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {msg && <p className="sub" role="status">{msg}</p>}
    </section>
  );
}

function Tools({ onChanged }) {
  const [csv, setCsv] = useState("");
  const [source, setSource] = useState("csv");
  const [optout, setOptout] = useState("");
  const [msg, setMsg] = useState("");
  const run = async (fn) => { setMsg("Working…"); try { setMsg(await fn()); onChanged?.(); } catch (e) { setMsg(e.message); } };
  return (
    <section className="card">
      <h3>Import, sync and opt-outs</h3>
      <label htmlFor="pi-file">CSV file (columns are matched like the lead import)</label>
      <input id="pi-file" type="file" accept=".csv,text/csv" onChange={(e) => e.target.files?.[0]?.text().then(setCsv)} />
      <label htmlFor="pi-text">…or paste CSV</label>
      <textarea id="pi-text" rows={4} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder="company,contact_name,title,email,website" />
      <label htmlFor="pi-src">Source name</label>
      <input id="pi-src" value={source} onChange={(e) => setSource(e.target.value)} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" disabled={!csv.trim()} onClick={() => run(async () => { const r = await prospects.importCsv(csv, source || "csv"); setCsv(""); return `Imported ${r.people} people and ${r.companies} companies from ${r.rows} rows.`; })}>Import into prospects</button>
        <button className="btn ghost" onClick={() => run(async () => { const r = await prospects.syncCrm(false); return r.busy ? "A sync is already running." : `Synced ${r.synced} changed leads (${r.unchanged} unchanged).`; })}>Sync from my leads now</button>
      </div>
      <label htmlFor="po-val">Opt-out an email or a whole domain</label>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input id="po-val" value={optout} onChange={(e) => setOptout(e.target.value)} placeholder="name@company.com or company.com" />
        <button className="btn ghost" disabled={optout.trim().length < 3} onClick={() => run(async () => { const r = await prospects.suppress(optout.trim(), "opt-out"); setOptout(""); return `Suppressed ${r.suppressed} (${r.people} people).`; })}>Suppress</button>
      </div>
      {msg && <p className="sub" role="status">{msg}</p>}
    </section>
  );
}

export default function ProspectAdmin({ onChanged }) {
  const [version, setVersion] = useState(0);
  const changed = () => { setVersion((v) => v + 1); onChanged?.(); };
  return <div className="ps-admin"><Spend /><Sources onChanged={onChanged} /><Tools onChanged={changed} /><OptOuts version={version} onChanged={onChanged} /></div>;
}
