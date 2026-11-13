// Prospect search: your own Apollo. Filters on the left, people / companies results, saved searches, bulk add to leads.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOwner, getRole } from "../api.js";
import { useConfirm, usePrompt } from "../Dialog.jsx";
import { saveBlob } from "../DownloadButton.jsx";
import { prospects } from "../prospectsApi.js";
import { istLabel } from "../time.js";
import ProspectAdmin from "./ProspectAdmin.jsx";
import { CompanyDrawer, PersonDrawer, StatusBadge } from "./ProspectDrawers.jsx";
import ProspectFilters, { countFilters, label } from "./ProspectFilters.jsx";
import "./prospects.css";

const EMPTY = { q: "", title: [], title_exclude: [], seniority: [], department: [], industry: [], employees: [], revenue: [], city: [], state: [],
  country: [], technologies: [], keywords: [], signal_kinds: [], signal_days: "", email_status: [], verified_only: false, has_email: false,
  has_phone: false, founded_min: "", founded_max: "", updated_days: "", source: [], not_in_crm: false };
const SORTS = [["relevance", "Best match"], ["updated", "Recently updated"], ["signal", "Signal strength"], ["employees", "Company size"], ["name", "Name"]];
const SIZE = 25;
const day = (iso) => (iso ? istLabel(iso).split(",")[0] : "—");

export default function ProspectSearch() {
  const [tab, setTab] = useState("people");
  const [filters, setFilters] = useState(EMPTY);
  const [sort, setSort] = useState("relevance");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [facets, setFacets] = useState(null);
  const [stats, setStats] = useState(null);
  const [saved, setSaved] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [person, setPerson] = useState(null);
  const [company, setCompany] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [lists, setLists] = useState([]);
  const confirm = useConfirm();
  const ask = usePrompt();
  const seq = useRef(0);
  const canAdd = getRole() !== "viewer";

  const refreshSide = useCallback(() => {
    prospects.stats().then(setStats).catch(() => {});
    prospects.facets().then(setFacets).catch(() => {});
  }, []);
  useEffect(() => {
    refreshSide();
    prospects.saved().then(setSaved).catch(() => {});
    api.lists().then(setLists).catch(() => {});
  }, []);

  const load = useCallback(() => {
    if (tab === "admin") return;
    const n = ++seq.current;
    const call = tab === "people" ? prospects.people : prospects.companies;
    call(filters, sort, page, SIZE).then((r) => { if (n === seq.current) { setData(r); setError(""); } })
      .catch((e) => { if (n === seq.current) setError(e.message); });
  }, [tab, filters, sort, page]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);

  const changeFilters = (f) => { setFilters(f); setPage(1); setPicked(new Set()); };
  const results = data?.results || [];
  const pages = data ? Math.max(1, Math.ceil(data.total / SIZE)) : 1;
  const allOnPage = results.length > 0 && results.every((r) => picked.has(r.id));
  const toggle = (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const ids = useMemo(() => [...picked], [picked]);

  const saveSearch = async () => {
    const name = await ask("Name this search", "");
    if (!name) return;
    try { const row = await prospects.saveSearch({ name, kind: tab === "companies" ? "companies" : "people", filters }); setSaved([row, ...saved]); setMsg(`Saved “${row.name}”.`); }
    catch (e) { setMsg(e.message); }
  };
  const openSaved = (id) => {
    const s = saved.find((x) => x.id === id);
    if (!s) return;
    setTab(s.kind === "companies" ? "companies" : "people");
    changeFilters({ ...EMPTY, ...s.filters });
  };
  const add = async (opts = {}) => {
    setBusy(true); setMsg("");
    try {
      const r = await prospects.addToLeads({ person_ids: ids, ...opts });
      const skipped = r.skipped.length ? ` Left out ${r.skipped.length}: ${[...new Set(r.skipped.map((s) => s.reason))].join("; ")}.` : "";
      setMsg(`Added ${r.added.length} new lead${r.added.length === 1 ? "" : "s"}; ${r.existing.length} already in your leads.${skipped}`
        + (r.sequence ? ` Email sequence started for ${r.sequence.started}.` : "") + (r.list_id ? " Saved to the list." : ""));
      setPicked(new Set()); load(); refreshSide();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const addToList = async () => {
    const names = lists.map((l) => l.name).join(", ");
    const name = await ask({ message: `List name (existing: ${names || "none yet"}). A new name makes a new list.`, title: "Add to a list" }, "");
    if (!name) return;
    const existing = lists.find((l) => l.name.toLowerCase() === name.trim().toLowerCase());
    await add(existing ? { list_id: existing.id } : { list_name: name.trim() });
    api.lists().then(setLists).catch(() => {});
  };
  const addAndStart = async () => {
    if (await confirm({ message: `Add ${ids.length} people to your leads and start the email sequence for those with a usable email? Every consent and suppression rule still applies.`, confirmLabel: "Add and start" })) add({ sequence: true });
  };
  const findEmails = async () => {
    setBusy(true); setMsg("");
    try {
      const est = await prospects.estimate(ids);
      setBusy(false);
      if (!est.targets) { setMsg("Everyone picked already has a verified email (or no company domain)."); return; }
      const text = `${est.targets} without a verified email: ${est.cached} can come free from the prospect database, ${est.to_look_up} need a lookup. `
        + `Estimated cost ${est.expected_cost} (at most ${est.max_cost}).` + (est.budget != null ? ` Budget left this month: ${est.budget_left}.` : "")
        + (est.note ? ` ${est.note}` : "") + (est.over_budget ? " This may pass your budget; lookups stop at the cap." : "");
      if (!isOwner()) { setMsg(text + " Ask the owner to run it."); return; }
      if (!(await confirm({ message: text, title: "Find emails", confirmLabel: "Run lookups" }))) return;
      setBusy(true);
      const r = await prospects.enrich(ids);
      setMsg(`Found ${r.found} email${r.found === 1 ? "" : "s"}; spent ${r.spent}.` + (r.errors?.length ? " " + r.errors.map((e) => e.message).join(" ") : ""));
      load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const exportCsv = async () => {
    try {
      const { blob, filename } = await prospects.exportCsv(ids.length ? { person_ids: ids } : { filters });
      saveBlob(blob, filename || "prospects.csv"); setMsg("Exported. Suppressed and erased people are never included.");
    } catch (e) { setMsg(e.message); }
  };

  return (
    <div className="prospects" data-testid="prospect-search">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <h2 style={{ margin: 0 }}>Prospect search</h2>
          <p className="sub" style={{ margin: "4px 0 0" }} data-testid="prospect-stats">
            {stats ? `${stats.people.toLocaleString()} people · ${stats.companies.toLocaleString()} companies · ${stats.verified.toLocaleString()} verified emails` : "Loading…"}
          </p>
        </div>
        <div className="row">
          <select className="ps-saved" aria-label="Saved searches" value="" onChange={(e) => openSaved(e.target.value)}>
            <option value="">Saved searches ({saved.length})</option>
            {saved.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {canAdd && <button className="btn ghost" onClick={saveSearch} disabled={!countFilters(filters)}>Save search</button>}
        </div>
      </div>
      <div className="tabs" role="tablist" style={{ marginTop: 12 }}>
        {[["people", "People"], ["companies", "Companies"], ...(isOwner() ? [["admin", "Spend & sources"]] : [])].map(([id, text]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => { setTab(id); setPage(1); setPicked(new Set()); setData(null); }}>{text}</button>
        ))}
      </div>
      {tab === "admin" ? <ProspectAdmin onChanged={refreshSide} /> : (
        <div className="ps-layout">
          <button type="button" className="btn ghost ps-filter-toggle" aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
            Filters{countFilters(filters) ? ` (${countFilters(filters)})` : ""}
          </button>
          <div className={`ps-side ${showFilters ? "open" : ""}`}>
            <ProspectFilters value={filters} onChange={changeFilters} facets={facets} kind={tab} onClear={() => changeFilters(EMPTY)} />
          </div>
          <div className="ps-main">
            <div className="toolbar">
              <input type="search" aria-label="Search names, titles, companies" placeholder={tab === "people" ? "Search names, titles, companies…" : "Search companies…"}
                value={filters.q} onChange={(e) => changeFilters({ ...filters, q: e.target.value })} />
              <select aria-label="Sort by" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
                {SORTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
            </div>
            {tab === "people" && picked.size > 0 && (
              <div className="bulkbar" data-testid="prospect-bulkbar">
                <b>{picked.size} picked</b>
                {canAdd && <button className="btn" disabled={busy} onClick={() => add()}>Add to leads</button>}
                {canAdd && <button className="btn ghost" disabled={busy} onClick={addToList}>Add to list…</button>}
                {canAdd && <button className="btn ghost" disabled={busy} onClick={addAndStart}>Add & start sequence</button>}
                {canAdd && <button className="btn ghost" disabled={busy} onClick={findEmails}>Find emails…</button>}
                {isOwner() && <button className="btn ghost" disabled={busy} onClick={exportCsv}>Export CSV</button>}
                <button className="btn ghost" onClick={() => setPicked(new Set())}>Clear</button>
              </div>
            )}
            {msg && <p className="tip" role="status" data-testid="prospect-msg">{msg}</p>}
            {error && <p className="tip error" role="alert">{error}</p>}
            <p className="sub" data-testid="prospect-total">{data ? `${data.total.toLocaleString()} ${tab === "people" ? "people" : "companies"} · ${data.took_ms} ms` : "Searching…"}</p>
            <div className="card table-card">
              {tab === "people" ? (
                <table className="ps-results">
                  <thead><tr>
                    <th className="pick"><input type="checkbox" aria-label="Select all on this page" checked={allOnPage}
                      onChange={() => setPicked((s) => { const n = new Set(s); results.forEach((r) => (allOnPage ? n.delete(r.id) : n.add(r.id))); return n; })} /></th>
                    <th>Name</th><th>Title</th><th>Company</th><th>Location</th><th>Email</th><th>Source</th><th>Updated</th>
                  </tr></thead>
                  <tbody>
                    {results.map((p) => (
                      <tr key={p.id} data-testid="prospect-row">
                        <td className="pick"><input type="checkbox" aria-label={`Pick ${p.full_name || p.email}`} checked={picked.has(p.id)} onChange={() => toggle(p.id)} /></td>
                        <td className="ps-name"><button className="linkish" onClick={() => setPerson(p.id)}><b>{p.full_name || p.email || "Unnamed"}</b></button>
                          {p.in_crm && <span className="pill" style={{ marginLeft: 6 }}>In leads</span>}
                          {p.in_crm && p.crm_status && <span className={`pill ${p.crm_status === "never" ? "ok" : p.crm_status === "tried" ? "warn" : ""}`} style={{ marginLeft: 4 }} data-testid="prospect-contact-status"
                            title="How far outreach got on their lead">{{ never: "Not contacted yet", tried: "Tried, no answer", connected: "Connected", replied: "Replied" }[p.crm_status]}</span>}
                          {!p.in_crm && !p.contact_hidden && <span className="pill ok" style={{ marginLeft: 6 }} title="Not a lead yet, so nobody has contacted them" data-testid="prospect-fresh">Fresh</span>}</td>
                        <td>{p.title || "—"}{p.seniority && <div><small className="sub">{label(p.seniority)}{p.department ? ` · ${label(p.department)}` : ""}</small></div>}</td>
                        <td>{p.company ? <button className="linkish" onClick={() => setCompany(p.company.id)}>{p.company.name || p.company.domain}</button> : "—"}
                          {p.company?.industry && <div><small className="sub">{p.company.industry}{p.company.employees ? ` · ${p.company.employees} staff` : ""}</small></div>}</td>
                        <td>{[p.city || p.company?.hq_city, p.country].filter(Boolean).join(", ") || "—"}</td>
                        <td className="ps-email"><span className="ps-email-text">{p.email || ""}</span> <StatusBadge status={p.email_status} confidence={p.email_confidence} />{p.has_phone && <span className="pill" title="Has a phone number">phone</span>}</td>
                        <td><small>{(p.sources || []).join(", ")}</small></td>
                        <td><small>{day(p.last_seen)}</small></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <table className="ps-results">
                  <thead><tr><th>Company</th><th>Industry</th><th>Employees</th><th>HQ</th><th>People</th><th>Signals</th><th>Technologies</th><th>Updated</th></tr></thead>
                  <tbody>
                    {results.map((c) => (
                      <tr key={c.id} data-testid="company-row">
                        <td className="ps-name"><button className="linkish" onClick={() => setCompany(c.id)}><b>{c.name || c.domain}</b></button>{c.domain && <div><small className="sub">{c.domain}</small></div>}</td>
                        <td>{c.industry || "—"}</td><td>{c.employees ?? "—"}</td><td>{[c.hq_city, c.hq_country].filter(Boolean).join(", ") || "—"}</td>
                        <td>{c.people_count}</td><td>{c.signal_score ? c.signal_score.toFixed(1) : "—"}</td>
                        <td><small>{(c.technologies || []).slice(0, 4).join(", ")}</small></td><td><small>{day(c.last_seen)}</small></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {data && results.length === 0 && <p className="empty">No matches. Loosen a filter, or sync your leads under Spend & sources.</p>}
            </div>
            <div className="row pager" style={{ justifyContent: "space-between", marginTop: 10 }}>
              <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span className="sub">Page {page} of {pages}</span>
              <button className="btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        </div>
      )}
      {person && <PersonDrawer id={person} onClose={() => setPerson(null)} onCompany={(id) => { setPerson(null); setCompany(id); }} onChanged={() => { load(); refreshSide(); }} />}
      {company && <CompanyDrawer id={company} onClose={() => setCompany(null)} onPerson={(id) => { setCompany(null); setPerson(id); }} />}
    </div>
  );
}
