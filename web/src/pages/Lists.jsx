import { useEffect, useState } from "react";
import { istLabel } from "../time.js";
import { api, canWrite } from "../api.js";
import { AssignDialog, ShareDialog, SplitDialog, canHandOut } from "./ListAssign.jsx";
import { ErrorState } from "../states.jsx";
import { useHashParam } from "../route.js";
import { useConfirm } from "../Dialog.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import ListDetail from "./ListDetail.jsx";
import { CompanyPicker, EnrichedBanner } from "./ListsParts.jsx";
import { Button, Card, DataTable, EmptyState, PageHeader, Select, Tabs } from "../ui/index.js";

const when = (iso) => (iso ? istLabel(iso) : "");
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
      <PageHeader title={admin ? "Lists" : "My lists"}
        description="Pick the companies or leads you want, save them as a list, then start email or LinkedIn outreach for that list only. Each list keeps its own journey."
        actions={<DownloadButton path={exportPath("lists")} label="Download lists" title="Every list with its numbers: open, reply and accept rates" testId="download-lists" />} />
      {error && <ErrorState message="Lists could not load." detail={error} onRetry={load} />}
      <EnrichedBanner onDone={load} />
      {admin && rows && (
        <div className="page-toolbar">
          <Tabs className="tight-tabs" label="Which lists" value={view} onChange={setView}
            items={[{ id: "all", label: "All lists", testId: "lists-all" }, { id: "unassigned", label: `Unassigned (${pool.length})`, testId: "lists-unassigned" }]} />
          <Select aria-label="Filter by assignee" value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="">Everyone</option>
            <option value="__pool">Admin pool</option>
            {assignees.map((n) => <option key={n} value={n}>{n}</option>)}
          </Select>
        </div>
      )}
      <div className="grid lists-grid">
        <Card className="table-card">
          {rows && rows.length > 0 && shownRows.length === 0 && <EmptyState icon="list" title={view === "unassigned" ? "Every list has an owner" : "No list matches"} />}
          {rows && rows.length === 0 && <EmptyState icon="list" title="No lists yet">{canWrite() ? "Choose companies on the left, or select leads on the Leads page and press “Save as list”." : "Lists you are given will show up here."}</EmptyState>}
          {rows && shownRows.length > 0 && (
            <DataTable tableClass="lists-table" label="Lists">
              <thead><tr><th className="c-list">List</th>{admin && <th className="c-assign">Assigned to</th>}<th className="c-leads">Leads</th><th className="c-stat">Email</th><th className="c-stat">LinkedIn</th><th className="c-acts"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {shownRows.map((r) => (
                  <tr key={r.id}>
                    <td className="c-list"><button className="linkish" title={r.name} onClick={() => setOpen(r.id)}><b>{r.name}</b></button><small className="block">{r.created_by} · {when(r.created_at)}</small></td>
                    {admin && <td className="c-assign" data-testid="list-assignee">{r.assigned_to || <small className="sub">Admin pool{r.unassigned_age_days != null ? ` · ${r.unassigned_age_days} day${r.unassigned_age_days === 1 ? "" : "s"} waiting` : ""}</small>}
                      {(r.shared_with || []).length > 0 && <small className="block sub">Shared with {r.shared_with.length}</small>}</td>}
                    <td className="c-leads">{r.stats.members}<small className="block">{r.stats.companies} cos</small></td>
                    <td className="c-stat"><small>{r.stats.email_started} started · {r.stats.opened} opened · {r.stats.replied} replied</small>
                      <small className="block" data-testid="list-email-rates"><b>{r.stats.open_rate ?? 0}%</b> open · <b>{r.stats.reply_rate ?? 0}%</b> reply</small></td>
                    <td className="c-stat"><small>{r.stats.linkedin_started} started · {r.stats.linkedin_accepted} accepted</small>
                      <small className="block"><b>{r.stats.accept_rate ?? 0}%</b> accept</small></td>
                    <td className="c-acts"><div className="row-actions">
                      {admin && <>
                        <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "assign", list: r })} aria-label={`Assign ${r.name}`}>Assign</Button>
                        <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "split", list: r })} aria-label={`Split ${r.name}`}>Split</Button>
                        <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "share", list: r })} aria-label={`Share ${r.name}`}>Share</Button>
                      </>}
                      {canWrite() && <Button variant="danger" size="sm" onClick={() => remove(r.id)} aria-label={`Delete ${r.name}`}>Delete</Button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          )}
        </Card>
        {canWrite() && <CompanyPicker onCreated={(r) => { load(); setOpen(r.id); }} />}
      </div>
      {dialog && admin && <ListDialog dialog={dialog} onClose={() => setDialog(null)} onDone={load} />}
      {open && <ListDetail id={open} onChanged={load} onClose={() => setOpen(null)} />}
    </>
  );
}
