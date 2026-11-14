import { canWrite } from "../api.js";
import { EmptyArt } from "../states.jsx";
import { sourceLabel, stageLabel } from "../labels.js";
import { istDateLabel, istLabel } from "../time.js";
import { showValue } from "./LeadCustomFields.jsx";
import PhoneTag from "./PhoneTag.jsx";
import { salesStageOf } from "../salesStage.js";
import { LinkedInCell } from "./LeadLinkedInStage.jsx";
import { Stage } from "../components/Abbr.jsx";
import { FirstTouchTimer } from "./LeadFirstTouch.jsx";
import { CONTACT_STATUSES, contactOf } from "../contactStatus.js";
import { AgedPill } from "./LeadAgeing.jsx";
import { attributionText } from "./leadFilters.js";
import { Button, Cell, DataTable } from "../ui/index.js";

function Pager({ total, page, pages, pageSize, setPage, setPageSize }) {
  return (
    <div className="pager" role="navigation" aria-label="Pages of leads">
      <span className="sub">{page * pageSize + 1}–{Math.min(total, (page + 1) * pageSize)} of {total}</span>
      <span className="row">
        <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
        <span>Page {page + 1} of {pages}</span>
        <Button variant="secondary" size="sm" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</Button>
        <select aria-label="Leads per page" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
          {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} per page</option>)}
        </select>
      </span>
    </div>
  );
}

// The Leads table inside a card: sticky header, fixed column widths, a combined Contact cell, and the pager.
export default function LeadTable({ leads, shown, visible, picked, setPicked, togglePick, sort, setSort, waitingOnly, tableFields, ageRules, setOpen, clearFilters, pager }) {
  const allPicked = shown.length > 0 && shown.every((l) => picked.includes(l.id));
  const sortHead = (key, label, cls = "") => (
    <th className={cls} aria-sort={sort === key ? (key === "score" ? "descending" : "ascending") : "none"}>
      <button className={`th-sort${sort === key ? " on" : ""}`} onClick={() => setSort(key)}>{label}{sort === key ? " ▾" : ""}</button>
    </th>
  );
  return (
    <div className="card table-card">
      {leads.length === 0 ? (
        <div className="empty"><EmptyArt name="leads" /><p>{canWrite() ? "No leads yet. Click “Import leads” to add your first list." : "No leads yet."}</p></div>
      ) : shown.length === 0 ? (
        <p className="empty">No leads match. <button className="linkish" onClick={clearFilters}>Clear the filters</button></p>
      ) : (
        <DataTable tableClass="leads-table" maxHeight="calc(100vh - 250px)" label="Leads">
          <thead><tr>
            {canWrite() && <th className="c-pick"><label className="pick-hit"><input type="checkbox" aria-label="Select all shown" checked={allPicked}
              onChange={() => setPicked(allPicked ? [] : shown.map((l) => l.id))} /></label></th>}
            {sortHead("company", "Company", "c-company")}<th className="c-contact">Contact</th>{sortHead("stage", "Stage", "c-stage")}<th className="c-sales">Sales stage</th><th className="c-status">Contact status</th>{sortHead("score", "Score", "c-score")}<th className="c-owner">Owner</th>{waitingOnly && <th>First touch</th>}<th className="c-tags">Tags</th>{tableFields.map((f) => <th key={f.key}>{f.label}</th>)}<th className="c-source">Source</th>{sortHead("activity", "Last activity", "c-date")}{sortHead("due", "Next task due", "c-date")}<th className="c-li">LinkedIn</th><th className="c-next">Next step</th>
          </tr></thead>
          <tbody>
            {visible.map((l) => {
              const c = contactOf(l);
              const name = l.company || l.domain || l.contact_name;
              return (
                <tr key={l.id} className="click" onClick={() => setOpen(l.id)}>
                  {canWrite() && <td className="pick c-pick" onClick={(e) => e.stopPropagation()}>
                    <label className="pick-hit"><input type="checkbox" aria-label={`Select ${name}`} checked={picked.includes(l.id)} onChange={() => togglePick(l.id)} /></label>
                  </td>}
                  <td className="c-company" data-label="Company"><button className="linkish" title={name} onClick={(e) => { e.stopPropagation(); setOpen(l.id); }}><b>{name}</b></button>{l.is_test && <span className="pill warn">Test</span>}</td>
                  <td className="c-contact" data-label="Contact"><Cell title={l.contact_name || "—"} sub={l.email || l.phone || ""} /><PhoneTag lead={l} /></td>
                  <td className="c-stage" data-label="Stage"><div className="pill-stack">{stageLabel(l.stage)}
                    {l.lead_quality && <span className="pill warn" data-testid="quality-pill" title={`Marked by ${l.lead_quality.by || "a caller"}${l.lead_quality.note ? `: ${l.lead_quality.note}` : ""}`}>{l.lead_quality.kind === "invalid" ? "Invalid lead" : "Consultant"}</span>}
                    {l.retry_exhausted && <span className="pill warn" title={`No answer after ${l.retry_exhausted.attempts} tries; taken off the call queue`}>Retries used up</span>}
                    {l.do_not_contact && <span className="pill err" title="On the do-not-contact list: never emailed, called or messaged">Do not contact</span>}
                    <AgedPill lead={l} rules={ageRules} /></div></td>
                  <td className="c-sales" data-label="Sales stage" data-testid="sales-stage-cell"><Stage status={salesStageOf(l)} full /></td>
                  <td className="c-status" data-label="Contact status" data-testid="contact-cell"><span className={`pill ${c.status === "never" ? "ok" : ["tried", "invalid", "consultant"].includes(c.status) ? "warn" : ""}`}>{CONTACT_STATUSES.find((x) => x[0] === c.status)[1]}{c.attempts ? ` · ${c.attempts}` : ""}</span></td>
                  <td className="c-score" data-label="Score"><span className={`pill ${l.band}`}>{l.score} · {l.band}</span></td>
                  <td className="c-owner" data-label="Owner">{l.owner || <small className="sub">—</small>}</td>
                  {waitingOnly && <td data-label="First touch"><FirstTouchTimer lead={l} /></td>}
                  <td className="c-tags" data-label="Tags">{(l.tags || []).length ? <div className="pill-stack">{(l.tags || []).map((t) => <span key={t} className="pill rec">{t}</span>)}</div> : <small className="sub">—</small>}</td>
                  {tableFields.map((f) => <td key={f.key} data-label={f.label}>{showValue(f, l.custom?.[f.key]) || <small className="sub">—</small>}</td>)}
                  <td className="c-source" data-label="Source" title={attributionText(l.attribution) || undefined}>
                    <Cell title={sourceLabel(l)} sub={attributionText(l.attribution)} />{l.inbound && <span className="pill ok">Inbound</span>}
                  </td>
                  <td className="c-date" data-label="Last activity"><small title={istLabel(l.last_activity_at)}>{istDateLabel(l.last_activity_at) || "—"}</small></td>
                  <td className="c-date" data-label="Next task due"><small className={l.next_task_due && new Date(l.next_task_due) < new Date() ? "pill err" : ""}>{l.next_task_due ? istLabel(l.next_task_due) : "—"}</small></td>
                  <td className="c-li" data-label="LinkedIn"><LinkedInCell lead={l} /></td>
                  <td className="c-next" data-label="Next step" title={l.suggestion.action}>{l.suggestion.action}</td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
      {shown.length > pager.pageSize && <Pager total={shown.length} {...pager} />}
    </div>
  );
}
