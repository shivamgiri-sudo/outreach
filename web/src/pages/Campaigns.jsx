import { useEffect, useState } from "react";
import { api } from "../api.js";
import { stageLabel } from "../labels.js";
import { istLabel } from "../time.js";
import CampaignWizard from "./CampaignWizard.jsx";
import { PreviewTable, RunReport, StatusPill } from "./CampaignReport.jsx";
import LeadDrawer from "./LeadDrawer.jsx";

function lastRunText(run) {
  if (!run) return "Not run yet";
  return `Last run ${istLabel(run.started_at)}: ${run.new} new, ${run.enrolled} enrolled${run.errors?.length ? `, ${run.errors.length} problem(s)` : ""}`;
}

function CampaignDetail({ id, go, onBack, onEdit }) {
  const [campaign, setCampaign] = useState(null);
  const [runs, setRuns] = useState([]);
  const [leads, setLeads] = useState([]);
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const load = () => Promise.all([api.campaign(id), api.campaignRuns(id), api.campaignLeads(id)])
    .then(([c, r, l]) => { setCampaign(c); setRuns(r); setLeads(l); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [id]);

  const act = async (kind) => {
    setBusy(kind); setError("");
    try {
      if (kind === "preview" || kind === "real") setResult(await api.runCampaign(id, kind === "preview"));
      if (kind === "resume") await api.resumeCampaign(id);
      if (kind === "pause") await api.pauseCampaign(id);
      if (kind === "delete") {
        if (!window.confirm("Delete this campaign? Its leads stay in your lead list.")) { setBusy(""); return; }
        await api.deleteCampaign(id); onBack(); return;
      }
      await load();
    } catch (e) { setError(e.message); }
    setBusy("");
  };

  if (!campaign) return error ? <div className="tip error" role="alert">{error}</div> : <p className="empty">Loading…</p>;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>{campaign.name}</h2><p className="sub"><StatusPill status={campaign.status} /> {campaign.stats?.leads_added || 0} leads added, {campaign.stats?.enrolled || 0} started in the sequence over {campaign.stats?.runs || 0} run(s).</p></div>
        <button className="btn ghost" onClick={onBack}>All campaigns</button>
      </div>
      <div className="row">
        <button className="btn ghost" disabled={!!busy} onClick={() => act("preview")}>{busy === "preview" ? "Searching…" : "Run now (preview)"}</button>
        <button className="btn" disabled={!!busy} onClick={() => act("real")}>{busy === "real" ? "Running…" : "Run for real"}</button>
        {campaign.status === "running"
          ? <button className="btn ghost" disabled={!!busy} onClick={() => act("pause")}>Pause daily runs</button>
          : <button className="btn ghost" disabled={!!busy} onClick={() => act("resume")} title="Mon–Fri, once a day, 09:00–18:00 IST">Run every weekday automatically</button>}
        <button className="btn ghost" onClick={() => onEdit(campaign)}>Edit</button>
        <button className="btn ghost danger" disabled={!!busy} onClick={() => act("delete")}>Delete</button>
      </div>
      {error && <div className="tip error" role="alert" style={{ marginTop: 12 }}>{error}</div>}
      {result && <><RunReport report={result} />{result.dry_run && <PreviewTable rows={result.preview} />}</>}

      <h3>Leads from this campaign ({leads.length})</h3>
      <div className="card table-card">
        {leads.length === 0 ? <p className="empty">No leads yet. Run the campaign to find some.</p> : (
          <table className="leads-table" data-testid="campaign-leads">
            <thead><tr><th>Company</th><th>Contact</th><th>Stage</th><th>Score</th><th>Ready for email?</th></tr></thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id} className="click" onClick={() => setOpen(l.id)}>
                  <td data-label="Company"><button className="linkish" onClick={(e) => { e.stopPropagation(); setOpen(l.id); }}><b>{l.company || l.domain}</b></button></td>
                  <td data-label="Contact">{l.contact_name || "—"}<br /><small>{l.email || l.phone || ""}</small></td>
                  <td data-label="Stage">{stageLabel(l.stage)}{l.sequence ? " · in sequence" : ""}</td>
                  <td data-label="Score"><span className={`pill ${l.band}`}>{l.score} · {l.band}</span></td>
                  <td data-label="Ready for email?">{l.sequence ? "Started" : l.campaign_check?.qualified ? <span className="pill ok">Yes</span>
                    : <small>{(l.campaign_check?.reasons || ["not checked yet"]).join("; ")}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h3>Runs</h3>
      <div className="card">
        {runs.length === 0 ? <p className="sub" style={{ margin: 0 }}>No runs yet.</p> : runs.map((r) => (
          <details key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
            <summary>{istLabel(r.started_at)}{r.trigger === "auto" ? " (automatic)" : ""}: {r.new} new, {r.qualified} ready, {r.enrolled} enrolled{r.errors?.length ? `, ${r.errors.length} problem(s)` : ""}</summary>
            <RunReport report={r} />
          </details>
        ))}
      </div>
      <p className="sub" style={{ marginTop: 12 }}>Emails go out only through the sequence: practice mode until "Send for real" is on, with daily caps, warm-up and business hours. <button className="linkish" onClick={() => go("outbox")}>Open Outbox</button></p>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

export default function Campaigns({ go }) {
  const [list, setList] = useState(null);
  const [view, setView] = useState({ mode: "list" });
  const [error, setError] = useState("");
  const load = () => api.campaigns().then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  if (view.mode === "wizard") {
    return <CampaignWizard initial={view.campaign} go={go} onSaved={load}
      onOpenLeads={(id) => setView({ mode: "detail", id })} onCancel={() => { load(); setView({ mode: "list" }); }} />;
  }
  if (view.mode === "detail") {
    return <CampaignDetail id={view.id} go={go} onBack={() => { load(); setView({ mode: "list" }); }}
      onEdit={(campaign) => setView({ mode: "wizard", campaign })} />;
  }
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>Campaigns</h2><p className="sub">One click finds companies that are hiring, fills in contacts, checks emails and, if you allow it, starts the email sequence.
          The caller gets an update after every run.</p></div>
        <button className="btn" onClick={() => setView({ mode: "wizard", campaign: null })}>New campaign</button>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      {!list ? <p className="empty">Loading…</p> : list.length === 0 ? (
        <div className="card"><p className="empty">No campaigns yet. Click “New campaign” to set up your first one, for example BPOs in Pune hiring customer support staff.</p></div>
      ) : (
        <div className="grid g2">
          {list.map((c) => (
            <div className="card" key={c.id} data-testid="campaign-card">
              <div className="row" style={{ justifyContent: "space-between" }}><b>{c.name}</b><StatusPill status={c.status} /></div>
              <p className="sub" style={{ margin: "6px 0 10px" }}>{lastRunText(c.last_run)}</p>
              <small className="sub-inline">{[c.target?.industries?.join(", "), c.target?.cities?.join(", "), c.target?.roles_hiring?.length ? `hiring ${c.target.roles_hiring.join(", ")}` : ""].filter(Boolean).join(" · ")}</small>
              <div className="row" style={{ marginTop: 10 }}>
                <button className="btn ghost" onClick={() => setView({ mode: "detail", id: c.id })}>Open</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
