import { useEffect, useMemo, useState } from "react";
import { istLabel } from "../time.js";
import { api, canWrite } from "../api.js";
import { AssignDialog, ShareDialog, SplitDialog, canHandOut } from "./ListAssign.jsx";
import { ErrorState } from "../states.jsx";
import { stageLabel } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";
import { useHashParam } from "../route.js";
import { useConfirm } from "../Dialog.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";

const when = (iso) => (iso ? istLabel(iso) : "");
const result = (verb, r) => `${verb} for ${r.started} of ${r.asked}.${r.refused.length ? " Left out: " + r.refused.map((x) => `${x.count} (${x.reason})`).join("; ") : ""}`;

// Step chips for one lead: how far outreach got.
function Journey({ j }) {
  const steps = [["Email started", j.email_started], ["Emailed", j.emailed], ["Opened", j.opened], ["Replied", j.replied],
    ["LinkedIn started", j.linkedin_started], ["Request sent", j.linkedin_sent], ["Accepted", j.linkedin_accepted], ["Interested", j.interested]];
  return <span className="chips">{steps.filter(([, on]) => on).map(([l]) => <span className="pill ok" key={l}>{l}</span>)}{!steps.some(([, on]) => on) && <span className="sub-inline">Not started</span>}</span>;
}

function CompanyPicker({ onCreated }) {
  const [companies, setCompanies] = useState([]);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState([]);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.listCompanies().then(setCompanies).catch((e) => setMsg(e.message)); }, []);
  const shown = useMemo(() => companies.filter((c) => c.company.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 200), [companies, q]);
  const toggle = (c) => setPicked((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
  const create = async () => {
    setMsg("");
    try { const r = await api.createList({ name: name || `${picked.length} companies`, companies: picked }); setPicked([]); setName(""); onCreated(r); } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Choose companies</h3>
      <p className="sub">Tick the companies you want to reach. Only they are put in the list; nobody else gets an email or a LinkedIn request.</p>
      <input aria-label="Search companies" placeholder="Search companies" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="picklist" role="group" aria-label="Companies" style={{ maxHeight: 260, overflow: "auto", margin: "8px 0" }}>
        {shown.length === 0 && <p className="empty">No companies yet.</p>}
        {shown.map((c) => (
          <label key={c.company} className="row" style={{ gap: 8, padding: "6px 0", cursor: "pointer" }}>
            <input type="checkbox" checked={picked.includes(c.company)} onChange={() => toggle(c.company)} />
            <b>{c.company}</b>
            <small className="sub-inline">{c.leads} lead{c.leads === 1 ? "" : "s"} · {c.with_email} email · {c.with_linkedin} LinkedIn{c.enriched ? ` · ${c.enriched} enriched` : ""}</small>
          </label>
        ))}
      </div>
      <div className="row">
        <input aria-label="List name" placeholder="Name this list (e.g. Noida BPOs, Nov)" value={name} onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
        <button className="btn" disabled={!picked.length} onClick={create}>Create list from {picked.length} compan{picked.length === 1 ? "y" : "ies"}</button>
      </div>
      {msg && <div className="tip error" role="alert">{msg}</div>}
    </div>
  );
}

function EnrichedBanner({ onDone }) {
  const [s, setS] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.enrichedSummary().then(setS).catch(() => {}); }, []);
  if (!s || !s.enriched) return null;
  const go = async () => {
    setBusy(true); setMsg("");
    try {
      const list = await api.createList({ name: `Enriched ${new Date().toISOString().slice(0, 10)}`, lead_ids: s.ready_ids });
      const r = await api.listStartEmail(list.id);
      setMsg(result("Email campaign started", r)); setConfirm(false); onDone();
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  return (
    <div className="card" style={{ marginBottom: 14, borderLeft: "4px solid var(--accent, #2563eb)" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div><b>{s.enriched} enriched leads</b> · <b>{s.email_ready}</b> from {s.companies} companies are ready for email and not started yet.</div>
        {!confirm && canWrite() && <button className="btn" disabled={!s.email_ready} onClick={() => setConfirm(true)}>Start email campaigning</button>}
      </div>
      {confirm && (
        <div className="tip" role="alert" style={{ marginTop: 8 }}>
          This puts {s.email_ready} leads in a new list and starts their email sequence. Sending still follows your daily cap, business hours and practice mode.
          To email only some of them, create a list from chosen companies below instead.
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={busy} onClick={go}>{busy ? "Starting…" : `Yes, start for ${s.email_ready}`}</button>
            <button className="btn ghost" onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        </div>
      )}
      {msg && <p className="sub" role="status">{msg}</p>}
    </div>
  );
}

function ListDetail({ id, onChanged, onClose }) {
  const [d, setD] = useState(null);
  const [picked, setPicked] = useState([]);
  const [msg, setMsg] = useState("");
  const [lead, setLead] = useHashParam("lead");
  const [askEmail, setAskEmail] = useState(false);
  const load = () => api.list(id).then(setD).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, [id]);
  if (!d) return <div className="card">{msg || "Loading…"}</div>;
  const only = picked.length ? picked : null;
  const run = async (fn, verb) => { setMsg(""); try { setMsg(result(verb, await fn(id, only))); setPicked([]); load(); onChanged(); } catch (e) { setMsg(e.message); } };
  const all = d.members.length > 0 && picked.length === d.members.length;
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h3 style={{ margin: 0 }}>{d.name}</h3><p className="sub" style={{ margin: 0 }}>Made by {d.created_by} · {when(d.created_at)} · {d.stats.members} leads in {d.stats.companies} companies · {d.stats.open_rate ?? 0}% open, {d.stats.reply_rate ?? 0}% reply, {d.stats.accept_rate ?? 0}% LinkedIn accept</p></div>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      {canWrite() && <div className="row" style={{ margin: "10px 0", flexWrap: "wrap" }}>
        <button className="btn" onClick={() => setAskEmail(true)}>Start email {picked.length ? `for ${picked.length} selected` : "for all"}</button>
        <button className="btn ghost" onClick={() => run(api.listStartLinkedIn, "LinkedIn started")}>Start LinkedIn {picked.length ? `for ${picked.length} selected` : "for all"}</button>
        {picked.length > 0 && <button className="btn ghost" onClick={async () => { await api.listMembers(id, { remove: picked }); setPicked([]); load(); onChanged(); }}>Remove selected from list</button>}
      </div>}
      {askEmail && (
        <ConfirmDialog title="Start the email sequence?" confirmLabel={`Yes, start for ${picked.length || d.members.length}`} onCancel={() => setAskEmail(false)}
          onConfirm={() => { setAskEmail(false); run(api.listStartEmail, "Email started"); }}>
          <p style={{ margin: "0 0 6px" }} data-testid="confirm-count">
            This is for <b>{picked.length || d.members.length}</b> lead(s) {picked.length ? <>you ticked in “{d.name}”.</> : <>— every lead in “{d.name}”, including any not on screen.</>}
          </p>
          <p style={{ margin: 0 }}>Leads with no email, already in the sequence, unsubscribed or on the do-not-contact list are skipped. Sending still follows your daily cap, business hours and practice mode.</p>
        </ConfirmDialog>
      )}
      {msg && <p className="sub" role="status">{msg}</p>}
      <div className="table-scroll">
        <table>
          <thead><tr>{canWrite() && <th><input type="checkbox" aria-label="Select all" checked={all} onChange={() => setPicked(all ? [] : d.members.map((m) => m.id))} /></th>}<th>Lead</th><th>Contact</th><th>Journey</th></tr></thead>
          <tbody>
            {d.members.map((m) => (
              <tr key={m.id}>
                {canWrite() && <td><input type="checkbox" aria-label={`Select ${m.company}`} checked={picked.includes(m.id)} onChange={() => setPicked((p) => (p.includes(m.id) ? p.filter((x) => x !== m.id) : [...p, m.id]))} /></td>}
                <td><button className="linkish" onClick={() => setLead(m.id)}><b>{m.company || m.contact_name}</b></button><br /><small>{m.contact_name} · {stageLabel(m.stage)}</small></td>
                <td><small>{m.email || "no email"}<br />{m.phone || ""}{m.linkedin_url ? " · LinkedIn" : ""}</small></td>
                <td><Journey j={m.journey} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>List journey</h4>
      <div className="timeline">{[...d.log].reverse().map((e, i) => <div key={i}><small>{when(e.at)} · {e.by}</small>{e.text}</div>)}</div>
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </div>
  );
}

const DIALOGS = { assign: AssignDialog, split: SplitDialog, share: ShareDialog };
function ListDialog({ dialog, onClose, onDone }) {
  const D = DIALOGS[dialog.kind];
  return D ? <D list={dialog.list} onClose={onClose} onDone={onDone} /> : null;
}

export default function Lists() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useHashParam("list");          // #lists?list=<id>
  const confirm = useConfirm();
  const [view, setView] = useState("all");          // admins: all | unassigned
  const [who, setWho] = useState("");               // assignee filter
  const [dialog, setDialog] = useState(null);       // { kind: assign | split | share, list }
  const admin = canHandOut();
  const load = () => api.lists().then(setRows).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const remove = async (id) => {
    if (!(await confirm({ message: "Delete this list? The leads stay; only the list goes.", confirmLabel: "Delete", danger: true }))) return;
    await api.deleteList(id); if (String(open) === String(id)) setOpen(null); load();
  };
  const pool = (rows || []).filter((r) => !r.assigned_to);
  const assignees = [...new Set((rows || []).map((r) => r.assigned_to).filter(Boolean))].sort();
  const shownRows = (rows || []).filter((r) => (view !== "unassigned" || !r.assigned_to) && (!who || (who === "__pool" ? !r.assigned_to : r.assigned_to === who)));
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>{admin ? "Lists" : "My lists"}</h2>
        <DownloadButton path={exportPath("lists")} label="Download lists" title="Every list with its numbers: open, reply and accept rates" testId="download-lists" />
      </div>
      <p className="sub">Pick the companies or leads you want, save them as a list, then start email or LinkedIn outreach for that list only. Each list keeps its own journey.</p>
      {error && <ErrorState message="Lists could not load." detail={error} onRetry={load} />}
      <EnrichedBanner onDone={load} />
      {admin && rows && (
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <div className="tabs" role="tablist" aria-label="Which lists">
            <button role="tab" aria-selected={view === "all"} className={view === "all" ? "on" : ""} onClick={() => setView("all")} data-testid="lists-all">All lists</button>
            <button role="tab" aria-selected={view === "unassigned"} className={view === "unassigned" ? "on" : ""} onClick={() => setView("unassigned")} data-testid="lists-unassigned">Unassigned ({pool.length})</button>
          </div>
          <select aria-label="Filter by assignee" value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="">Everyone</option>
            <option value="__pool">Admin pool</option>
            {assignees.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
      )}
      <div className="grid g2">
        {canWrite() && <CompanyPicker onCreated={(r) => { load(); setOpen(r.id); }} />}
        <div className="card table-card" style={{ padding: 0, minWidth: 0 }}>
          {rows && rows.length > 0 && shownRows.length === 0 && <p className="empty">{view === "unassigned" ? "Every list has an owner." : "No list matches."}</p>}
          {rows && rows.length === 0 && <p className="empty">{canWrite() ? "No lists yet. Choose companies on the left, or select leads on the Leads page and press “Save as list”." : "No lists yet."}</p>}
          {rows && shownRows.length > 0 && (
            <div className="table-scroll"><table>
              <thead><tr><th>List</th>{admin && <th>Assigned to</th>}<th>Leads</th><th>Email</th><th>LinkedIn</th><th /></tr></thead>
              <tbody>
                {shownRows.map((r) => (
                  <tr key={r.id}>
                    <td><button className="linkish" onClick={() => setOpen(r.id)}><b>{r.name}</b></button><br /><small>{r.created_by} · {when(r.created_at)}</small></td>
                    {admin && <td data-testid="list-assignee">{r.assigned_to || <small className="sub">Admin pool{r.unassigned_age_days != null ? ` · ${r.unassigned_age_days} day${r.unassigned_age_days === 1 ? "" : "s"} waiting` : ""}</small>}
                      {(r.shared_with || []).length > 0 && <><br /><small className="sub">Shared with {r.shared_with.length}</small></>}</td>}
                    <td>{r.stats.members}<br /><small>{r.stats.companies} cos</small></td>
                    <td><small>{r.stats.email_started} started · {r.stats.opened} opened · {r.stats.replied} replied</small>
                      <br /><small data-testid="list-email-rates"><b>{r.stats.open_rate ?? 0}%</b> open · <b>{r.stats.reply_rate ?? 0}%</b> reply</small></td>
                    <td><small>{r.stats.linkedin_started} started · {r.stats.linkedin_accepted} accepted</small>
                      <br /><small><b>{r.stats.accept_rate ?? 0}%</b> accept</small></td>
                    <td><div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                      {admin && <>
                        <button className="btn ghost" onClick={() => setDialog({ kind: "assign", list: r })} aria-label={`Assign ${r.name}`}>Assign</button>
                        <button className="btn ghost" onClick={() => setDialog({ kind: "split", list: r })} aria-label={`Split ${r.name}`}>Split</button>
                        <button className="btn ghost" onClick={() => setDialog({ kind: "share", list: r })} aria-label={`Share ${r.name}`}>Share</button>
                      </>}
                      {canWrite() && <button className="btn ghost" onClick={() => remove(r.id)} aria-label={`Delete ${r.name}`}>Delete</button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
      </div>
      {dialog && admin && <ListDialog dialog={dialog} onClose={() => setDialog(null)} onDone={load} />}
      {open && <ListDetail id={open} onChanged={load} onClose={() => setOpen(null)} />}
    </>
  );
}
