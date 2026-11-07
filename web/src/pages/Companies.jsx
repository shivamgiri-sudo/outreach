import { useEffect, useRef, useState } from "react";
import { api, canWrite } from "../api.js";
import { istLabel } from "../time.js";
import { ErrorState } from "../states.jsx";
import { leadTerm, stageLabel } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";
import CompanyHealth from "./CompanyHealth.jsx";

const PAGE = 25;
const SORTS = [["last_touch", "Last touch"], ["contacts", "Contacts"], ["score", "Best score"], ["health", "Company health"], ["name", "Name"]];
const HEALTH_BAND = { strong: "ok", steady: "", "weak / unknown": "warn" };
const when = (iso) => (iso ? istLabel(iso) : "never");

function useNarrow() {
  const query = "(max-width: 760px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return undefined;
    const on = () => setNarrow(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return narrow;
}

function StatusPills({ c }) {
  return (
    <span className="chips">
      <span className="pill">{stageLabel(c.best_stage)}</span>
      {c.engaged && <span className="pill ok">Engaged account</span>}
      {c.health && <span className={`pill ${HEALTH_BAND[c.health.band] || ""}`} title="Company health">Health {c.health.score}</span>}
    </span>
  );
}

function Stat({ label, value }) {
  return <div className="card" style={{ padding: "10px 12px", margin: 0 }}><small className="sub">{label}</small><div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div></div>;
}

function CompanyDetail({ companyKey, onClose }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const [shown, setShown] = useState(40);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const panel = useRef(null);
  const leadOpen = useRef(false);
  leadOpen.current = Boolean(lead);

  const load = () => api.company(companyKey).then((r) => { setD(r); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { setD(null); setShown(40); setMsg(""); load(); }, [companyKey]);
  useEffect(() => {
    const prev = document.activeElement;
    const onKey = (e) => { if (e.key === "Escape" && !leadOpen.current) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);
  useEffect(() => { if (d) panel.current?.querySelector("h2")?.focus(); }, [d?.key]);

  const ids = d ? d.contacts_list.map((c) => c.id) : [];
  const saveList = async (start) => {
    setBusy(true); setMsg("");
    try {
      const list = await api.createList({ name: `${d.name} contacts`.slice(0, 80), lead_ids: ids });
      if (!start) { setMsg(`Saved as list “${list.name}” with ${list.lead_ids.length} contacts. Open Lists to use it.`); return; }
      const r = await api.listStartEmail(list.id);
      setMsg(`Email started for ${r.started} of ${r.asked} (list “${list.name}”).${r.refused?.length ? " Left out: " + r.refused.map((x) => `${x.count} (${x.reason})`).join("; ") : ""}`);
      load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby="company-title" ref={panel} data-testid="company-detail">
        {!d ? <p>{error || "Loading…"}</p> : (
          <>
            <div className="drawer-head">
              <div style={{ minWidth: 0 }}>
                <h2 id="company-title" tabIndex={-1} style={{ overflowWrap: "anywhere" }}>{d.name}</h2>
                <StatusPills c={d} />
                {d.domain && <div><small className="sub">{d.domain}</small></div>}
              </div>
              <button className="btn ghost" onClick={onClose}>Close</button>
            </div>
            {d.warnings.length > 0 && (
              <div className="tip error" role="alert" data-testid="overlap-warning">
                <b>Same-day contact at one company.</b>
                {d.warnings.map((w, i) => <div key={i}>{w.text}</div>)}
                <small>Consider pausing one sequence so this employer hears from you once a day.</small>
              </div>
            )}
            <div style={{ gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", display: "grid" }}>
              <Stat label="Contacts" value={d.contacts} />
              <Stat label="Touches" value={d.touches} />
              <Stat label="Replies" value={d.replies} />
              <Stat label={leadTerm("open_roles")} value={d.open_roles} />
              <Stat label="LinkedIn accepted" value={d.linkedin_accepted} />
              <Stat label="Open tasks" value={d.open_tasks} />
            </div>
            <p className="sub" style={{ marginTop: 8 }}>
              Furthest stage: <b>{stageLabel(d.best_stage)}</b> · last touch {when(d.last_touch)}
              {d.owners.length > 0 && <> · owner{d.owners.length > 1 ? "s" : ""}: {d.owners.join(", ")}</>}
            </p>
            {canWrite() && <div className="row" style={{ flexWrap: "wrap", margin: "10px 0" }}>
              <button className="btn" disabled={busy || !ids.length} onClick={() => saveList(true)}>Start email for all contacts</button>
              <button className="btn ghost" disabled={busy || !ids.length} onClick={() => saveList(false)}>Save as list</button>
            </div>}
            {msg && <p className="sub" role="status">{msg}</p>}

            <h3>People ({d.contacts_list.length})</h3>
            <div data-testid="company-contacts">
              {d.contacts_list.map((c) => (
                <div key={c.id} className="row" style={{ justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)", flexWrap: "wrap", gap: 6 }}>
                  <div style={{ minWidth: 0 }}>
                    <button className="linkish" onClick={() => setLead(c.id)}><b>{c.contact_name || c.email || "Unnamed contact"}</b></button>
                    {c.title && <small className="sub-inline"> · {c.title}</small>}
                    <div><small className="sub">
                      {c.email || "no email"} · last touch {when(c.last_touch)}
                      {c.next_task && <> · next: {c.next_task.title}{c.next_task.due_at ? ` (${istLabel(c.next_task.due_at)})` : ""}</>}
                    </small></div>
                  </div>
                  <span className="chips">
                    <span className="pill">{stageLabel(c.stage)}</span>
                    <span className="pill" title="Buying-signal score">{c.score}</span>
                    {c.in_sequence && <span className="pill rec">Email sequence</span>}
                    {c.in_linkedin && <span className="pill rec">LinkedIn</span>}
                  </span>
                </div>
              ))}
            </div>

            {d.health_lead_id && <CompanyHealth lead={{ id: d.health_lead_id }} />}

            <h3>Everything that happened ({d.timeline_total})</h3>
            {d.timeline.length === 0 ? <p className="sub">No activity yet.</p> : (
              <div className="timeline" data-testid="company-timeline">
                {d.timeline.slice(0, shown).map((t, i) => (
                  <div key={i}>
                    <small>{t.at ? istLabel(t.at) : ""} · {t.contact.name} · {t.type}{t.direction === "in" ? " (in)" : ""}{t.status ? ` · ${t.status}` : ""}</small>
                    <b>{t.title}</b>
                    {t.body && <div className="sub" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{String(t.body).slice(0, 280)}{String(t.body).length > 280 ? "…" : ""}</div>}
                  </div>
                ))}
              </div>
            )}
            {d.timeline.length > shown && <button className="btn ghost" onClick={() => setShown((n) => n + 40)}>Show more</button>}
          </>
        )}
      </div>
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}

export default function Companies() {
  const narrow = useNarrow();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("last_touch");
  const [order, setOrder] = useState("desc");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);

  useEffect(() => { const t = setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 250); return () => clearTimeout(t); }, [q]);
  const load = () => api.companies({ q: query, sort, order, limit: PAGE, offset }).then((r) => { setData(r); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [query, sort, order, offset]);

  const items = data?.items || [];
  const total = data?.total || 0;
  return (
    <section>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div><h2>Companies</h2><p className="sub">Every person at one employer together: furthest stage, touches, replies and health. Click a company for its people and full history.</p></div>
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, margin: "8px 0 12px" }}>
        <input aria-label="Search companies" placeholder="Search company, domain or person" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 220px", minWidth: 0 }} />
        <select aria-label="Sort by" value={sort} onChange={(e) => { setSort(e.target.value); setOffset(0); setOrder(e.target.value === "name" ? "asc" : "desc"); }}>
          {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button className="btn ghost" aria-label={`Order: ${order === "desc" ? "highest first" : "lowest first"}`} onClick={() => { setOrder(order === "desc" ? "asc" : "desc"); setOffset(0); }}>
          {order === "desc" ? "High → low" : "Low → high"}
        </button>
      </div>
      {error && <ErrorState message="Could not load companies" detail={error} onRetry={load} />}
      {!data && !error && <p className="empty">Loading…</p>}
      {data && items.length === 0 && <p className="empty">{query ? "No company matches that search." : "No companies yet. Import leads to see them grouped by employer."}</p>}
      {items.length > 0 && (narrow ? (
        <div data-testid="companies-cards">
          {items.map((c) => (
            <button key={c.key} className="card" style={{ display: "block", width: "100%", textAlign: "left", margin: "0 0 10px", cursor: "pointer", font: "inherit", color: "inherit" }} onClick={() => setOpen(c.key)}>
              <b style={{ overflowWrap: "anywhere" }}>{c.name}</b> {c.domain && <small className="sub-inline">{c.domain}</small>}
              <div style={{ margin: "6px 0" }}><StatusPills c={c} /></div>
              <small className="sub">{c.contacts} contact{c.contacts === 1 ? "" : "s"} · {c.touches} touches · {c.replies} repl{c.replies === 1 ? "y" : "ies"} · last {when(c.last_touch)}</small>
            </button>
          ))}
        </div>
      ) : (
        <div className="card" style={{ overflowX: "auto", padding: 0 }}>
          <table data-testid="companies-table">
            <thead><tr><th>Company</th><th>Status</th><th>Contacts</th><th>Touches</th><th>Replies</th><th>Last touch</th><th>{leadTerm("open_roles")}</th><th>LinkedIn accepted</th><th>Owner</th></tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.key}>
                  <td><button className="linkish" onClick={() => setOpen(c.key)}><b>{c.name}</b></button>{c.domain && <><br /><small className="sub">{c.domain}</small></>}</td>
                  <td><StatusPills c={c} />{c.active_sequences > 1 && <div><small className="sub">{c.active_sequences} in active sequences</small></div>}</td>
                  <td>{c.contacts}</td><td>{c.touches}</td><td>{c.replies}</td>
                  <td><small>{when(c.last_touch)}</small></td><td>{c.open_roles || "–"}</td><td>{c.linkedin_accepted}</td>
                  <td><small>{c.owners.join(", ") || "–"}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {total > PAGE && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 10, flexWrap: "wrap" }}>
          <small className="sub">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</small>
          <div className="row">
            <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <button className="btn ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        </div>
      )}
      {data && total <= PAGE && total > 0 && <small className="sub">{total} compan{total === 1 ? "y" : "ies"}</small>}
      {open && <CompanyDetail companyKey={open} onClose={() => { setOpen(null); load(); }} />}
    </section>
  );
}
