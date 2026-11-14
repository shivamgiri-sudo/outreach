import { useEffect, useMemo, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import LeadDrawer from "./LeadDrawer.jsx";
import ImportWizard from "./ImportWizard.jsx";
import { useHashParam } from "../route.js";
import FindLeadsDrawer, { takePendingLeadIds } from "./FindLeads.jsx";
import LeadBulkBar from "./LeadBulkBar.jsx";
import { BulkNotice, HandoverPanel } from "./LeadBulkParts.jsx";
import { useLeadFields } from "./LeadCustomFields.jsx";
import { AgeingTools, useAgeingRules } from "./LeadAgeing.jsx";
import { useSourceRegistry } from "../labels.js";
import LeadToolbar from "./LeadToolbar.jsx";
import LeadTable from "./LeadTable.jsx";
import LeadToolPanels from "./LeadToolPanels.jsx";
import LeadSummary from "./LeadSummary.jsx";
import useLeadFilters from "./useLeadFilters.js";
import { Button, PageHeader } from "../ui/index.js";
import HelpLink from "../components/HelpLink.jsx";

// Re-exported because other screens (quick search) import them from here.
export { phoneMatches, HAS_FIELDS, hasDetail, attributionText } from "./leadFilters.js";

export default function Leads({ go }) {
  const [leads, setLeads] = useState([]);
  const [open, setOpen] = useHashParam("lead");          // #leads?lead=<id>: refresh, Back and shared links keep the drawer
  const [ageRules, setAgeRules] = useAgeingRules();
  const { fields: customFields } = useLeadFields();
  const tableFields = customFields.filter((f) => f.show_in_table);
  const [owners, setOwners] = useState([]);
  const [me, setMe] = useState({ name: "", email: "" });
  useEffect(() => {
    api.leadOwners().then((r) => setOwners(r.owners)).catch(() => {});
    api.me().then((r) => setMe({ name: r.name, email: r.email })).catch(() => {});
  }, []);
  useSourceRegistry();
  const [picked, setPicked] = useState([]);
  const [finding, setFinding] = useState(false);
  const [panel, setPanel] = useState("");               // "" | scrape | jobs | paste
  const [showWizard, setShowWizard] = useState(false);
  const [message, setMessage] = useState("");
  const [bulkNotice, setBulkNotice] = useState(null);       // { text, undoId } after a bulk change
  const [quality, setQuality] = useState(null);

  const load = () => {
    api.leads().then(setLeads).catch((e) => setMessage(e.message));
    api.quality().then(setQuality).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const lf = useLeadFilters({ leads, me, customFields, initialOnlyIds: useMemo(() => takePendingLeadIds(), []) });
  const { f, set, shown, sort, setSort, onlyIds, setOnlyIds } = lf;
  const allTags = useMemo(() => [...new Set(leads.flatMap((l) => l.tags || []).map(String))].sort((a, b) => a.localeCompare(b)), [leads]);

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const pages = Math.max(1, Math.ceil(shown.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  useEffect(() => { setPage(0); }, [JSON.stringify(f), sort, pageSize]);
  const visible = shown.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const togglePick = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const [spaces, setSpaces] = useState([]);
  useEffect(() => { api.workspaces().then(setSpaces).catch(() => {}); }, []);

  const toggle = (name) => { setPanel(panel === name ? "" : name); setShowWizard(false); };
  return (
    <>
      <PageHeader title={<>Leads <HelpLink chapter="leads" label="Leads" /></>} description="Sorted by buying-signal score. Click a lead to see what to say."
        actions={canWrite() && <>
          <Button variant="secondary" onClick={() => toggle("scrape")}>Find contacts on a website</Button>
          <Button variant="secondary" onClick={() => toggle("jobs")}>Read job pages</Button>
          <Button variant="secondary" onClick={() => toggle("paste")}>Quick paste</Button>
          <Button icon="upload" onClick={() => { setShowWizard(!showWizard); setPanel(""); }}>Import leads</Button>
        </>} />
      {onlyIds && (
        <div className="tip" role="status">
          Showing the {onlyIds.length} lead(s) from your last Find &amp; enrich. <button className="linkish" onClick={() => setOnlyIds(null)}>Show all leads</button>
        </div>
      )}
      {finding && <FindLeadsDrawer go={go} onClose={() => { setFinding(false); load(); }} onOpenLeads={(ids) => { setOnlyIds(ids); lf.resetBasic(); }} />}
      {showWizard && canWrite() && <ImportWizard onClose={() => { setShowWizard(false); load(); }} onDone={load} />}
      {message && <div className="tip" role="status">{message} <button className="linkish" aria-label="Dismiss message" onClick={() => setMessage("")}>✕</button></div>}
      {canWrite() && <LeadToolPanels which={panel} onClose={() => setPanel("")} setMessage={setMessage} load={load} />}

      <LeadSummary quality={quality} go={go} onFind={() => setFinding(true)} setMessage={setMessage} load={load} />

      {leads.length > 0 && (
        <LeadToolbar leads={leads} shown={shown} f={f} set={set} owners={owners} allTags={allTags} rules={ageRules} customFields={customFields}
          viewFilters={lf.viewFilters} applyView={lf.applyView} clearFilters={lf.clearFilters} filtered={lf.filtered} customOn={lf.customOn} nMore={lf.nMore}
          tools={<AgeingTools shown={shown} kind={f.ageing} rules={ageRules} setRules={setAgeRules} onNotice={setMessage} onDone={load} />} />
      )}
      {picked.length > 0 && canWrite() && (
        <LeadBulkBar picked={picked} pageIds={visible.map((l) => l.id)} owners={owners} spaces={spaces} onClear={() => setPicked([])}
          onDone={(msg, changed, undoId) => { if (undoId) { setMessage(""); setBulkNotice({ text: msg, undoId }); } else { setBulkNotice(null); setMessage(msg); } if (changed) { setPicked([]); load(); } }} />
      )}
      <BulkNotice key={bulkNotice?.undoId} notice={bulkNotice} onUndone={load} onClose={() => setBulkNotice(null)} />

      <LeadTable leads={leads} shown={shown} visible={visible} picked={picked} setPicked={setPicked} togglePick={togglePick} sort={sort} setSort={setSort}
        waitingOnly={f.waitingOnly} tableFields={tableFields} ageRules={ageRules} setOpen={setOpen} clearFilters={lf.clearFilters}
        pager={{ page: safePage, pages, pageSize, setPage, setPageSize }} />
      <HandoverPanel owners={owners} onDone={(n, changed) => { setBulkNotice(n); if (changed) load(); }} />
      {open && <LeadDrawer id={open} ids={shown.map((l) => l.id)} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
