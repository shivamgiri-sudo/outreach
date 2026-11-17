import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import "../campaigns.css";
import { istLabel } from "../time.js";
import CampaignWizard from "./CampaignWizard.jsx";
import { PreviewTable, RunReport, StatusPill } from "./CampaignReport.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import { useHashParam } from "../route.js";
import { useConfirm } from "../Dialog.jsx";
import { CampaignLeadsTable, OutcomeLine, OutcomeTiles } from "./CampaignOutcomes.jsx";
import { Button, Card, CardHeader, EmptyState, PageHeader, Skeleton } from "../ui/index.js";

function lastRunText(run) {
  if (!run) return "Not run yet";
  return `Last run ${istLabel(run.started_at)}: ${run.new} new, ${run.enrolled} enrolled${run.errors?.length ? `, ${run.errors.length} problem(s)` : ""}`;
}

function CampaignDetail({ id, go, onBack, onEdit }) {
  const [campaign, setCampaign] = useState(null);
  const [runs, setRuns] = useState([]);
  const [leadCount, setLeadCount] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [open, setOpen] = useHashParam("lead");
  const confirm = useConfirm();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const load = () => Promise.all([api.campaign(id), api.campaignRuns(id)])
    .then(([c, r]) => { setCampaign(c); setRuns(r); setRefresh((n) => n + 1); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [id]);

  const act = async (kind) => {
    setBusy(kind); setError("");
    try {
      if (kind === "preview" || kind === "real") setResult(await api.runCampaign(id, kind === "preview"));
      if (kind === "resume") await api.resumeCampaign(id);
      if (kind === "pause") await api.pauseCampaign(id);
      if (kind === "delete") {
        if (!(await confirm({ message: "Delete this campaign? Its leads stay in your lead list.", confirmLabel: "Delete", danger: true }))) { setBusy(""); return; }
        await api.deleteCampaign(id); onBack(); return;
      }
      await load();
    } catch (e) { setError(e.message); }
    setBusy("");
  };

  if (!campaign) return error ? <div className="tip error" role="alert">{error}</div> : <div className="camp-skel" aria-busy="true"><Skeleton height={32} width="40%" /><Skeleton height={80} /><Skeleton height={200} /></div>;
  const st = campaign.stats || {};
  return (
    <>
      <PageHeader title={campaign.name}
        description={<><StatusPill status={campaign.status} /> {st.leads_added || 0} leads added, {st.enrolled || 0} started in the sequence over {st.runs || 0} run(s).</>}
        actions={<Button variant="secondary" onClick={onBack}>All campaigns</Button>} />
      {isOwner() && <div className="row u-mb-16">
        <Button variant="secondary" disabled={!!busy} onClick={() => act("preview")}>{busy === "preview" ? "Searching…" : "Run now (preview)"}</Button>
        <Button disabled={!!busy} onClick={() => act("real")}>{busy === "real" ? "Running…" : "Run for real"}</Button>
        {campaign.status === "running"
          ? <Button variant="secondary" disabled={!!busy} onClick={() => act("pause")}>Pause daily runs</Button>
          : <Button variant="secondary" disabled={!!busy} onClick={() => act("resume")} title="Mon–Fri, once a day, 09:00–18:00 IST">Run every weekday automatically</Button>}
        <Button variant="secondary" onClick={() => onEdit(campaign)}>Edit</Button>
        <Button variant="danger" disabled={!!busy} onClick={() => act("delete")}>Delete</Button>
      </div>}
      {error && <div className="tip error u-mb-12" role="alert">{error}</div>}
      <h3>Outcomes</h3>
      <OutcomeTiles o={campaign.outcomes} />
      {result && <><RunReport report={result} />{result.dry_run && <PreviewTable rows={result.preview} />}</>}

      <section className="camp-section">
        <CardHeader title={`Leads from this campaign (${leadCount})`}
          actions={<span className="dl-group">
            <DownloadButton path={exportPath("campaign_leads", { campaign_id: id })} label="Download leads" disabled={leadCount === 0} testId="download-campaign-leads" />
            <DownloadButton path={exportPath("campaign_leads", { campaign_id: id }, "xlsx")} label="Excel" title="The same leads as an Excel file" disabled={leadCount === 0} testId="download-campaign-leads-xlsx" />
          </span>} />
        <CampaignLeadsTable id={id} onOpen={setOpen} onTotal={setLeadCount} refreshKey={refresh} />
      </section>

      <section className="camp-section">
        <CardHeader title="Runs"
          actions={<DownloadButton path={exportPath("campaign_runs", { campaign_id: id })} label="Download run report" disabled={runs.length === 0}
            title="Every run's numbers and problems, one row per run" testId="download-campaign-runs" />} />
        {runs.length === 0 ? <Card><EmptyState icon="history" title="No runs yet">Run a preview first: it shows what would be added and saves nothing.</EmptyState></Card> : (
          <div className="camp-runs">
            {runs.map((r) => (
              <details key={r.id}>
                <summary>{istLabel(r.started_at)}{r.trigger === "auto" ? " (automatic)" : ""}: {r.new} new, {r.qualified} ready, {r.enrolled} enrolled{r.errors?.length ? `, ${r.errors.length} problem(s)` : ""}</summary>
                <div><RunReport report={r} /></div>
              </details>
            ))}
          </div>
        )}
      </section>
      <p className="sub u-mt-16">Emails go out only through the sequence: practice mode until "Send for real" is on, with daily caps, warm-up and business hours. <button className="linkish" onClick={() => go("outbox")}>Open Outbox</button></p>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

export default function Campaigns({ go }) {
  const [list, setList] = useState(null);
  const [view, setView] = useState({ mode: "list" });     // the wizard; the detail view lives in the address (#campaigns?campaign=<id>)
  const [openId, setOpenId] = useHashParam("campaign");
  const [error, setError] = useState("");
  const load = () => api.campaigns().then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  if (view.mode === "wizard") {
    return <CampaignWizard initial={view.campaign} go={go} onSaved={load}
      onOpenLeads={(id) => { setView({ mode: "list" }); setOpenId(id); }} onCancel={() => { load(); setView({ mode: "list" }); }} />;
  }
  if (openId) {
    return <CampaignDetail key={openId} id={openId} go={go} onBack={() => { load(); setOpenId(null); }}
      onEdit={(campaign) => setView({ mode: "wizard", campaign })} />;
  }
  const owner = isOwner();
  return (
    <>
      <PageHeader title="Campaigns" description="One click finds companies that are hiring, fills in contacts, checks emails and, if you allow it, starts the email sequence. The caller gets an update after every run."
        actions={owner && <Button icon="plus" onClick={() => setView({ mode: "wizard", campaign: null })}>New campaign</Button>} />
      {error && <ErrorState message="Campaigns could not load." detail={error} onRetry={load} />}
      {!list && !error ? <div className="camp-grid" aria-busy="true">{[0, 1, 2].map((i) => <Card key={i}><Skeleton height={20} width="60%" /><Skeleton height={14} style={{ marginTop: 12 }} /><Skeleton height={48} style={{ marginTop: 12 }} /></Card>)}</div> : list && list.length === 0 ? (
        <Card><EmptyState icon="rocket" title="No campaigns yet">
          {owner ? "Press “New campaign” above to set up your first one, for example BPOs in Pune hiring customer support staff. You can preview it before anything is saved." : "When an owner sets one up, it will show here."}</EmptyState></Card>
      ) : list && (
        <div className="camp-grid">
          {list.map((c) => (
            <Card className="camp-card" key={c.id} data-testid="campaign-card">
              <div className="camp-card-head"><h3>{c.name}</h3><StatusPill status={c.status} /></div>
              <p className="camp-last">{lastRunText(c.last_run)}</p>
              <OutcomeLine o={c.outcomes} />
              <p className="camp-target">{[c.target?.industries?.join(", "), c.target?.cities?.join(", "), c.target?.roles_hiring?.length ? `hiring ${c.target.roles_hiring.join(", ")}` : ""].filter(Boolean).join(" · ") || "No targeting set"}</p>
              <div className="camp-foot"><Button variant="secondary" onClick={() => setOpenId(c.id)}>Open</Button></div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
