// The People and Companies result tables of Prospect search (fixed column widths, truncation with tooltips).
import { istLabel } from "../time.js";
import { Badge, Cell, DataTable } from "../ui/index.js";
import { StatusBadge } from "./ProspectDrawers.jsx";
import { label } from "./ProspectFilters.jsx";

const day = (iso) => (iso ? istLabel(iso).split(",")[0] : "—");
const CRM_TEXT = { never: "Not contacted yet", tried: "Tried, no answer", connected: "Connected", replied: "Replied", invalid: "Invalid lead", consultant: "Consultant lead" };

export function PeopleTable({ results, picked, toggle, allOnPage, togglePage, onPerson, onCompany }) {
  return (
    <DataTable tableClass="ps-results ps-people" label="People results">
      <thead><tr>
        <th className="pick"><input type="checkbox" aria-label="Select all on this page" checked={allOnPage} onChange={togglePage} /></th>
        <th className="c-name">Name</th><th className="c-title">Title</th><th className="c-co">Company</th><th className="c-loc">Location</th>
        <th className="c-email">Email</th><th className="c-src">Source</th><th className="c-upd">Updated</th>
      </tr></thead>
      <tbody>
        {results.map((p) => {
          const name = p.full_name || p.email || "Unnamed";
          const where = [p.city || p.company?.hq_city, p.country].filter(Boolean).join(", ");
          const sources = (p.sources || []).join(", ");
          return (
            <tr key={p.id} data-testid="prospect-row">
              <td className="pick"><input type="checkbox" aria-label={`Pick ${name}`} checked={picked.has(p.id)} onChange={() => toggle(p.id)} /></td>
              <td className="ps-name" data-label="Name">
                <button className="linkish" title={name} onClick={() => onPerson(p.id)}><b>{name}</b></button>
                <div className="pill-stack">
                  {p.in_crm && <Badge>In leads</Badge>}
                  {p.in_crm && p.crm_status && (
                    <Badge tone={p.crm_status === "never" ? "success" : ["tried", "invalid", "consultant"].includes(p.crm_status) ? "warning" : "neutral"}
                      data-testid="prospect-contact-status" title="How far outreach got on their lead">{CRM_TEXT[p.crm_status]}</Badge>
                  )}
                  {!p.in_crm && !p.contact_hidden && <Badge tone="success" title="Not a lead yet, so nobody has contacted them" data-testid="prospect-fresh">Fresh</Badge>}
                </div>
              </td>
              <td data-label="Title"><Cell title={p.title || "—"} sub={p.seniority ? `${label(p.seniority)}${p.department ? ` · ${label(p.department)}` : ""}` : ""} /></td>
              <td data-label="Company">
                {p.company ? <button className="linkish ps-co" title={p.company.name || p.company.domain} onClick={() => onCompany(p.company.id)}>{p.company.name || p.company.domain}</button> : "—"}
                {p.company?.industry && <div className="ps-sub" title={p.company.industry}>{p.company.industry}{p.company.employees ? ` · ${p.company.employees} staff` : ""}</div>}
              </td>
              <td data-label="Location"><span className="ps-clip" title={where}>{where || "—"}</span></td>
              <td className="ps-email" data-label="Email">
                <span className="ps-email-text" title={p.email || ""}>{p.email || ""}</span>
                <div className="pill-stack"><StatusBadge status={p.email_status} confidence={p.email_confidence} />{p.has_phone && <Badge title="Has a phone number">phone</Badge>}</div>
              </td>
              <td data-label="Source"><span className="ps-clip" title={sources}>{sources || "—"}</span></td>
              <td data-label="Updated"><small>{day(p.last_seen)}</small></td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}

export function CompanyTable({ results, onCompany }) {
  return (
    <DataTable tableClass="ps-results ps-companies" label="Company results">
      <thead><tr><th className="c-name">Company</th><th className="c-ind">Industry</th><th className="num c-num">Employees</th><th className="c-loc">HQ</th><th className="num c-num">People</th><th className="num c-num">Signals</th><th className="c-tech">Technologies</th><th className="c-upd">Updated</th></tr></thead>
      <tbody>
        {results.map((c) => {
          const hq = [c.hq_city, c.hq_country].filter(Boolean).join(", ");
          const tech = (c.technologies || []).slice(0, 4).join(", ");
          return (
            <tr key={c.id} data-testid="company-row">
              <td className="ps-name" data-label="Company">
                <button className="linkish" title={c.name || c.domain} onClick={() => onCompany(c.id)}><b>{c.name || c.domain}</b></button>
                {c.domain && <div className="ps-sub" title={c.domain}>{c.domain}</div>}
              </td>
              <td data-label="Industry"><span className="ps-clip" title={c.industry || ""}>{c.industry || "—"}</span></td>
              <td className="num" data-label="Employees">{c.employees ?? "—"}</td>
              <td data-label="HQ"><span className="ps-clip" title={hq}>{hq || "—"}</span></td>
              <td className="num" data-label="People">{c.people_count}</td>
              <td className="num" data-label="Signals">{c.signal_score ? c.signal_score.toFixed(1) : "—"}</td>
              <td data-label="Technologies"><span className="ps-clip" title={tech}>{tech || "—"}</span></td>
              <td data-label="Updated"><small>{day(c.last_seen)}</small></td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}
