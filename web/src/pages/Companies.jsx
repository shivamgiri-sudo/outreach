import { useEffect, useRef, useState } from "react";
import { api, canWrite } from "../api.js";
import { istLabel } from "../time.js";
import { ErrorState } from "../states.jsx";
import { leadTerm, stageLabel } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";
import CompanyHealth from "./CompanyHealth.jsx";
import { PageHeader, Card, CardHeader, DataTable, Button, Badge, Input, Select, EmptyState, Skeleton } from "../ui/index.js";
import "../inbox-pages.css";

const PAGE = 25;
const SORTS = [["last_touch", "Last touch"], ["contacts", "Contacts"], ["score", "Best score"], ["health", "Company health"], ["name", "Name"]];
const HEALTH_BAND = { strong: "success", steady: "neutral", "weak / unknown": "warning" };
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
    <span className="chips u-m0">
      <Badge>{stageLabel(c.best_stage)}</Badge>
      {c.engaged && <Badge tone="success">Engaged account</Badge>}
      {c.health && <Badge tone={HEALTH_BAND[c.health.band] || "neutral"} title="Company health">Health {c.health.score}</Badge>}
    </span>
  );
}

function Stat({ label, value }) {
  return <div className="co-stat"><small>{label}</small><b>{value}</b></div>;
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
        {!d ? (error ? <p className="error">{error}</p> : <div className="stack" aria-busy="true"><Skeleton height={32} width="60%" /><Skeleton height={80} /><Skeleton height={160} /></div>) : (
          <>
            <div className="drawer-head">
              <div className="u-min0">
                <h2 id="company-title" tabIndex={-1} className="u-anywhere">{d.name}</h2>
                <StatusPills c={d} />
                {d.domain && <div><small className="sub">{d.domain}</small></div>}
              </div>
              <Button variant="secondary" onClick={onClose}>Close</Button>
            </div>
            {d.warnings.length > 0 && (
              <div className="tip error" role="alert" data-testid="overlap-warning">
                <b>Same-day contact at one company.</b>
                {d.warnings.map((w, i) => <div key={i}>{w.text}</div>)}
                <small>Consider pausing one sequence so this employer hears from you once a day.</small>
              </div>
            )}
            <div className="co-stats">
              <Stat label="Contacts" value={d.contacts} />
              <Stat label="Touches" value={d.touches} />
              <Stat label="Replies" value={d.replies} />
              <Stat label={leadTerm("open_roles")} value={d.open_roles} />
              <Stat label="LinkedIn accepted" value={d.linkedin_accepted} />
              <Stat label="Open tasks" value={d.open_tasks} />
            </div>
            <p className="sub u-mt-8">
              Furthest stage: <b>{stageLabel(d.best_stage)}</b> · last touch {when(d.last_touch)}
              {d.owners.length > 0 && <> · owner{d.owners.length > 1 ? "s" : ""}: {d.owners.join(", ")}</>}
            </p>
            {canWrite() && <div className="drawer-actions">
              <Button disabled={busy || !ids.length} onClick={() => saveList(true)}>Start email for all contacts</Button>
              <Button variant="secondary" disabled={busy || !ids.length} onClick={() => saveList(false)}>Save as list</Button>
            </div>}
            {msg && <p className="sub" role="status">{msg}</p>}

            <h3 className="u-mt-16">People ({d.contacts_list.length + (d.more_people?.length || 0)})</h3>
            <div className="co-people" data-testid="company-contacts">
              {d.contacts_list.map((c) => (
                <div key={c.id} className="co-person">
                  <div className="u-min0">
                    <button className="linkish" onClick={() => setLead(c.id)}><b>{c.contact_name || c.email || "Unnamed contact"}</b></button>
                    {c.title && <small className="sub-inline"> · {c.title}</small>}
                    <div><small className="sub">
                      {c.email || "no email"} · last touch {when(c.last_touch)}
                      {c.next_task && <> · next: {c.next_task.title}{c.next_task.due_at ? ` (${istLabel(c.next_task.due_at)})` : ""}</>}
                    </small></div>
                  </div>
                  <span className="chips">
                    <Badge>{stageLabel(c.stage)}</Badge>
                    <Badge title="Buying-signal score">{c.score}</Badge>
                    {c.in_sequence && <Badge tone="info">Email sequence</Badge>}
                    {c.in_linkedin && <Badge tone="info">LinkedIn</Badge>}
                  </span>
                </div>
              ))}
              {(d.more_people || []).map((c, i) => (
                <div key={`more-${i}`} className="co-person" data-testid="company-more-person">
                  <div className="u-min0">
                    <b>{c.contact_name || c.email || "Unnamed contact"}</b>
                    {c.title && <small className="sub-inline"> · {c.title}</small>}
                    <div><small className="sub">
                      {c.email || "no email"}{c.phone ? ` · ${c.phone}` : ""} · kept on <button className="linkish" onClick={() => setLead(c.kept_on)}>{c.kept_on_name}</button>'s record
                    </small></div>
                  </div>
                  <span className="chips"><Badge title="Found with another lead at this company; not a separate lead yet">Also at this company</Badge></span>
                </div>
              ))}
            </div>

            {d.health_lead_id && <CompanyHealth lead={{ id: d.health_lead_id }} />}

            <h3 className="u-mt-16">Everything that happened ({d.timeline_total})</h3>
            {d.timeline.length === 0 ? <p className="sub">No activity yet.</p> : (
              <div className="timeline" data-testid="company-timeline">
                {d.timeline.slice(0, shown).map((t, i) => (
                  <div key={i}>
                    <small>{t.at ? istLabel(t.at) : ""} · {t.contact.name} · {t.type}{t.direction === "in" ? " (in)" : ""}{t.status ? ` · ${t.status}` : ""}</small>
                    <b>{t.title}</b>
                    {t.body && <div className="sub u-prewrap u-anywhere">{String(t.body).slice(0, 280)}{String(t.body).length > 280 ? "…" : ""}</div>}
                  </div>
                ))}
              </div>
            )}
            {d.timeline.length > shown && <Button variant="secondary" onClick={() => setShown((n) => n + 40)}>Show more</Button>}
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
      <PageHeader title="Companies" description={<>Every person at one employer together: furthest stage, touches, replies and health. Click a company for its people and full history.</>} />
      <div className="filter-bar">
        <div className="grow"><Input icon="search" aria-label="Search companies" placeholder="Search company, domain or person" value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} /></div>
        <select aria-label="Sort by" value={sort} onChange={(e) => { setSort(e.target.value); setOffset(0); setOrder(e.target.value === "name" ? "asc" : "desc"); }}>
          {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <Button variant="secondary" aria-label={`Order: ${order === "desc" ? "highest first" : "lowest first"}`} onClick={() => { setOrder(order === "desc" ? "asc" : "desc"); setOffset(0); }}>
          {sort === "name" ? (order === "desc" ? "Z → A" : "A → Z") : order === "desc" ? "High → low" : "Low → high"}
        </Button>
      </div>
      {error && <ErrorState message="Could not load companies" detail={error} onRetry={load} />}
      {!data && !error && <Card className="table-card" aria-busy="true"><div className="stack u-p-16"><Skeleton height={28} /><Skeleton height={28} /><Skeleton height={28} /><Skeleton height={28} /></div></Card>}
      {data && items.length === 0 && (
        <Card><EmptyState icon="building" title={query ? "No company matches that search" : "No companies yet"}>
          {query ? "Try a shorter name, or a domain such as acme.com." : "Import leads and they are grouped here by employer."}</EmptyState></Card>
      )}
      {items.length > 0 && (narrow ? (
        <div className="co-cards" data-testid="companies-cards">
          {items.map((c) => (
            <button key={c.key} className="card co-card" onClick={() => setOpen(c.key)}>
              <b className="u-anywhere">{c.name}</b> {c.domain && <small className="sub-inline">{c.domain}</small>}
              <div className="u-m-8-0"><StatusPills c={c} /></div>
              <small className="sub">{c.contacts} contact{c.contacts === 1 ? "" : "s"} · {c.touches} touches · {c.replies} repl{c.replies === 1 ? "y" : "ies"} · last {when(c.last_touch)}</small>
            </button>
          ))}
        </div>
      ) : (
        <Card className="table-card">
          <DataTable data-testid="companies-table">
            <thead><tr><th>Company</th><th>Status</th><th className="num">Contacts</th><th className="num">Touches</th><th className="num">Replies</th><th>Last touch</th><th className="num">{leadTerm("open_roles")}</th><th className="num">LinkedIn accepted</th><th>Owner</th></tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.key}>
                  <td><button className="linkish" onClick={() => setOpen(c.key)}><b>{c.name}</b></button>{c.domain && <><br /><small className="sub">{c.domain}</small></>}</td>
                  <td><StatusPills c={c} />{c.active_sequences > 1 && <div><small className="sub">{c.active_sequences} in active sequences</small></div>}</td>
                  <td className="num">{c.contacts}</td><td className="num">{c.touches}</td><td className="num">{c.replies}</td>
                  <td><small>{when(c.last_touch)}</small></td><td className="num">{c.open_roles || "–"}</td><td className="num">{c.linkedin_accepted}</td>
                  <td><small>{c.owners.join(", ") || "–"}</small></td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </Card>
      ))}
      {total > PAGE && (
        <div className="co-pager">
          <small className="sub">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</small>
          <div className="row">
            <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
            <Button variant="secondary" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
          </div>
        </div>
      )}
      {data && total <= PAGE && total > 0 && <small className="sub">{total} compan{total === 1 ? "y" : "ies"}</small>}
      {open && <CompanyDetail companyKey={open} onClose={() => { setOpen(null); load(); }} />}
    </section>
  );
}
