// Person and company drawers of Prospect search, with "where did this come from" per field.
import { useEffect, useState } from "react";
import { isOwner } from "../api.js";
import { useConfirm } from "../Dialog.jsx";
import DrawerShell from "../DrawerShell.jsx";
import { prospects } from "../prospectsApi.js";
import { istLabel } from "../time.js";
import { label } from "./ProspectFilters.jsx";

const STATUS_CLASS = { verified: "ok", valid: "ok", catch_all: "warn", risky: "warn", invalid: "hot", unknown: "" };
const when = (iso) => (iso ? istLabel(iso) : "—");
const FIELD_LABEL = { full_name: "Name", first_name: "First name", last_name: "Last name", company_domain: "Company domain", source_url: "Found at",
  linkedin_url: "LinkedIn", hq_city: "HQ city", hq_state: "HQ state", hq_country: "HQ country", employees_range: "Employee range",
  revenue_range: "Revenue", sub_industry: "Sub-industry" };
const fieldName = (k) => FIELD_LABEL[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ").replace(/\./g, ": ");

export function StatusBadge({ status, confidence }) {
  if (!status) return <span className="sub">no email</span>;
  return <span className={`pill ${STATUS_CLASS[status] || ""}`} title={confidence != null ? `Confidence ${confidence}%` : undefined}>
    {label(status)}{confidence != null && status !== "invalid" ? ` · ${confidence}%` : ""}
  </span>;
}

function Provenance({ provenance }) {
  const keys = Object.keys(provenance || {}).filter((k) => provenance[k]?.entries?.length);
  if (!keys.length) return <p className="sub">No field history yet.</p>;
  return (
    <div className="prov-list" data-testid="provenance">
      {keys.map((k) => {
        const f = provenance[k];
        return (
          <details key={k} className="prov-field">
            <summary>
              <b>{fieldName(k)}</b> <span className="prov-val">{String(f.value ?? "—")}</span>
              <small className="sub"> · {f.entries.length} source{f.entries.length > 1 ? "s" : ""}{f.last_verified ? ` · verified ${when(f.last_verified)}` : ""}</small>
            </summary>
            <table className="prov-table">
              <thead><tr><th>Value</th><th>Source</th><th>Seen</th><th>Confidence</th></tr></thead>
              <tbody>
                {f.entries.map((e, i) => (
                  <tr key={i} className={e.chosen ? "chosen" : ""}>
                    <td style={{ overflowWrap: "anywhere" }}>{String(e.value)}{e.chosen && <span className="pill ok" style={{ marginLeft: 6 }}>shown</span>}{e.disabled && <span className="pill">source off</span>}</td>
                    <td>{e.source}{e.status ? ` (${label(e.status)})` : ""}</td><td>{when(e.seen_at)}</td><td>{e.confidence ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        );
      })}
    </div>
  );
}

export function PersonDrawer({ id, onClose, onCompany, onChanged }) {
  const [p, setP] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const confirm = useConfirm();
  useEffect(() => { setP(null); prospects.person(id).then(setP).catch((e) => setError(e.message)); }, [id]);
  const act = async (fn, text) => {
    try { await fn(); setMsg(text); onChanged?.(); } catch (e) { setMsg(e.message); }
  };
  return (
    <DrawerShell onClose={onClose} labelledBy="pp-title" testId="prospect-person">
      {!p ? <p>{error || "Loading…"}</p> : (
        <>
          <div className="drawer-head">
            <div style={{ minWidth: 0 }}>
              <h2 id="pp-title" tabIndex={-1} style={{ overflowWrap: "anywhere" }}>{p.full_name || p.email || "Unnamed"}</h2>
              <div className="sub">{p.title || "No title"}{p.company && <> · <button className="linkish" onClick={() => onCompany(p.company.id)}>{p.company.name || p.company.domain}</button></>}</div>
            </div>
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
          <div className="pd-grid">
            <div><small className="sub">Email</small><div style={{ overflowWrap: "anywhere" }}>{p.email || "—"} <StatusBadge status={p.email_status} confidence={p.email_confidence} /></div></div>
            <div><small className="sub">Phone</small><div>{(p.phones || []).join(", ") || "—"}</div></div>
            <div><small className="sub">Seniority · department</small><div>{label(p.seniority) || "—"} · {label(p.department) || "—"}</div></div>
            <div><small className="sub">Location</small><div>{[p.city, p.state, p.country].filter(Boolean).join(", ") || "—"}</div></div>
            <div><small className="sub">Last updated · verified</small><div>{when(p.last_seen)} · {when(p.last_verified)}</div></div>
            <div><small className="sub">Sources</small><div>{(p.sources || []).join(", ") || "—"}</div></div>
          </div>
          {p.linkedin_url && <p><a href={p.linkedin_url} target="_blank" rel="noreferrer noopener">LinkedIn profile</a></p>}
          {p.suppressed && <div className="tip" role="note">Suppressed (opted out): never exported or added to leads.</div>}
          {p.crm_leads?.length > 0 && <p className="sub">Already in your leads.</p>}
          <h3>Where did this come from</h3>
          <Provenance provenance={p.provenance} />
          {isOwner() && (
            <div className="row" style={{ marginTop: 16 }}>
              {p.email && !p.suppressed && <button className="btn ghost" onClick={() => act(() => prospects.suppress(p.email, "opt-out"), "Suppressed.")}>Suppress (opt-out)</button>}
              <button className="btn ghost" onClick={async () => {
                if (await confirm({ message: "Erase this person from the prospect database? Their contacts are kept only as hashes so no source can add them back.", danger: true, confirmLabel: "Erase" })) {
                  act(() => prospects.erase(p.id), "Erased."); onClose();
                }
              }}>Erase (delete data)</button>
            </div>
          )}
          {msg && <p className="sub" role="status">{msg}</p>}
        </>
      )}
    </DrawerShell>
  );
}

export function CompanyDrawer({ id, onClose, onPerson }) {
  const [c, setC] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { setC(null); prospects.company(id).then(setC).catch((e) => setError(e.message)); }, [id]);
  return (
    <DrawerShell onClose={onClose} labelledBy="pc-title" testId="prospect-company">
      {!c ? <p>{error || "Loading…"}</p> : (
        <>
          <div className="drawer-head">
            <div style={{ minWidth: 0 }}>
              <h2 id="pc-title" tabIndex={-1} style={{ overflowWrap: "anywhere" }}>{c.name || c.domain}</h2>
              <div className="sub">{[c.domain, c.industry, c.hq_city].filter(Boolean).join(" · ")}</div>
            </div>
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
          <div className="pd-grid">
            <div><small className="sub">Employees</small><div>{c.employees ?? c.employees_range ?? "—"}</div></div>
            <div><small className="sub">Revenue</small><div>{c.revenue_range || "—"}</div></div>
            <div><small className="sub">Founded</small><div>{c.founded || "—"}</div></div>
            <div><small className="sub">Signal strength</small><div>{c.signal_score ? c.signal_score.toFixed(1) : "0"}</div></div>
            <div><small className="sub">Last updated</small><div>{when(c.last_seen)}</div></div>
            <div><small className="sub">Sources</small><div>{(c.sources || []).join(", ") || "—"}</div></div>
          </div>
          {c.description && <p style={{ overflowWrap: "anywhere" }}>{c.description}</p>}
          {c.website && <p><a href={c.website} target="_blank" rel="noreferrer noopener">{c.website}</a></p>}
          {c.technologies?.length > 0 && <><h3>Technologies</h3><div className="chips">{c.technologies.map((t) => <span key={t} className="pill">{t}</span>)}</div></>}
          <h3>Signals ({c.signals.length})</h3>
          {c.signals.length === 0 ? <p className="sub">No signals yet.</p> : (
            <div className="timeline" data-testid="company-signals">
              {c.signals.map((s, i) => (
                <div key={i}><small>{when(s.at)} · {label(s.kind)} · {s.source}</small>
                  <b>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a> : s.title}</b></div>
              ))}
            </div>
          )}
          <h3>People ({c.people.length})</h3>
          <div data-testid="company-people">
            {c.people.length === 0 ? <p className="sub">No people stored yet.</p> : c.people.map((p) => (
              <div key={p.id} className="row" style={{ justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                <div style={{ minWidth: 0 }}>
                  <button className="linkish" onClick={() => onPerson(p.id)}><b>{p.full_name || p.email}</b></button>
                  <div><small className="sub">{p.title || "—"}</small></div>
                </div>
                <StatusBadge status={p.email_status} confidence={p.email_confidence} />
              </div>
            ))}
          </div>
          <h3>Where did this come from</h3>
          <Provenance provenance={c.provenance} />
        </>
      )}
    </DrawerShell>
  );
}
