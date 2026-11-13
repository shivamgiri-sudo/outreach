import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { STAGE_LABELS, stageLabel } from "../labels.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { useConfirm, usePrompt } from "../Dialog.jsx";
import { BulkLinkedInAccount } from "./LeadLinkedInStage.jsx";

const VERBS = {
  enroll: "Started the email sequence for", stage: "Moved", owner: "Assigned", tag_add: "Tagged", tag_remove: "Removed the tag from",
  do_not_contact: "Put on the do-not-contact list:", delete: "Deleted", list_add: "Added to the list:",
};

// One line about a bulk result: how many worked and why the rest were left out.
export function bulkSummary(action, r) {
  const left = r.reasons.map((x) => `${x.count} ${x.reason}`).join("; ");
  return `${VERBS[action] || "Done for"} ${r.ok} of ${r.asked} lead(s).${left ? ` Left out: ${left}.` : ""}`;
}

const offPage = (picked, pageIds) => picked.filter((id) => !pageIds.includes(id)).length;

function CountLine({ picked, pageIds }) {
  const away = offPage(picked, pageIds);
  return (
    <p style={{ margin: "0 0 6px" }} data-testid="confirm-count">
      This is for <b>{picked.length}</b> selected lead(s).
      {away > 0 && <> <b>{away}</b> of them are on other pages of this filter, not on screen now.</>}
    </p>
  );
}

// The actions bar for ticked leads. Every change goes through POST /api/leads/bulk, which answers per lead.
export default function LeadBulkBar({ picked, pageIds, owners, spaces, onDone, onClear }) {
  const [busy, setBusy] = useState("");
  const promptFor = usePrompt();
  const confirm = useConfirm();
  const [lists, setLists] = useState([]);
  const [ask, setAsk] = useState(null);        // { action, value, title, label, danger }
  const [tagText, setTagText] = useState("");
  const [force, setForce] = useState(false);
  const caller = canWrite();
  useEffect(() => { if (caller) api.lists().then(setLists).catch(() => setLists([])); }, [caller]);

  const run = async (action, value = null) => {
    setBusy(action);
    try {
      const r = await api.bulkLeads(picked, action, value, action === "stage" && force);
      onDone(bulkSummary(action, r), action === "delete" || r.ok > 0, r.undo_id || null);
    } catch (e) { onDone(e.message, false); }
    setBusy(""); setAsk(null); setForce(false);
  };

  const tagThen = async (action) => {
    const tag = tagText.trim();
    const add = action === "tag_add";
    const yes = await confirm({ title: `${add ? "Add" : "Remove"} the tag “${tag}” ${add ? "to" : "from"} ${picked.length} lead${picked.length === 1 ? "" : "s"}?`,
      message: "Leads that already have (or do not have) the tag are left out. You can undo this for 24 hours.", confirmLabel: add ? "Yes, add the tag" : "Yes, remove it" });
    if (yes) run(action, tag);
  };

  const confirmThen = (action, value, title, label, danger = false) => setAsk({ action, value, title, label, danger });

  const moveTo = (id) => {
    const name = spaces.find((w) => w.id === id)?.name || id;
    setAsk({ action: "workspace", value: id, title: `Move to workspace “${name}”?`, label: "Yes, move them" });
  };
  const doMove = async (id) => {
    setBusy("ws");
    try {
      const r = await api.moveToWorkspace(id, picked); onDone(`${r.moved} lead(s) moved.`, true);
      window.dispatchEvent(new Event("workspaces-changed"));          // the menu's workspace counts follow
    } catch (e) { onDone(e.message, false); }
    setBusy(""); setAsk(null);
  };
  const saveAsList = async () => {
    const name = await promptFor({ message: "Name this list (for example: Noida BPOs, Nov)", defaultValue: `Selected ${picked.length} leads` });
    if (!name) return;
    setBusy("list");
    try { const r = await api.createList({ name, lead_ids: picked }); onDone(`Saved “${r.name}” with ${r.lead_ids.length} leads. Open Lists to start email or LinkedIn for it only.`, true); }
    catch (e) { onDone(e.message, false); }
    setBusy("");
  };
  const bulkLinkedIn = async () => {
    setBusy("li");
    try {
      const list = await api.createList({ name: `LinkedIn ${new Date().toISOString().slice(0, 10)} (${picked.length})`, lead_ids: picked });
      const r = await api.listStartLinkedIn(list.id);
      const refused = r.refused.reduce((n, x) => n + x.count, 0);
      onDone(`LinkedIn outreach: ${r.started} started, ${r.looking_up || 0} are being looked up (their LinkedIn profile is searched first), ${refused} refused${refused ? " (" + r.refused.map((x) => `${x.count}: ${x.reason}`).join("; ") + ")" : ""}.`, true);
    } catch (e) { onDone(e.message, false); }
    setBusy("");
  };

  return (
    <div className="bulkbar" role="region" aria-label="Actions for selected leads" data-testid="bulkbar">
      <b>{picked.length} selected</b>
      {caller && <>
        <button className="btn" disabled={busy === "enroll"} onClick={() => confirmThen("enroll", null, "Start the email sequence?", `Yes, start for ${picked.length}`)}>
          {busy === "enroll" ? "Starting…" : "Start email sequence"}</button>
        <button className="btn ghost" disabled={busy === "li"} onClick={bulkLinkedIn}>{busy === "li" ? "Starting…" : "Start LinkedIn outreach"}</button>
        <BulkLinkedInAccount picked={picked} onDone={onDone} />
        <button className="btn ghost" disabled={busy === "list"} onClick={saveAsList}>Save as list</button>
        <select aria-label="Change stage of selected leads" value="" onChange={(e) => e.target.value && confirmThen("stage", e.target.value, `Move to ${stageLabel(e.target.value)}?`, "Yes, move them")} style={{ width: "auto" }}>
          <option value="">Change stage…</option>
          {Object.keys(STAGE_LABELS).map((k) => <option key={k} value={k}>{stageLabel(k)}</option>)}
        </select>
        <select aria-label="Assign selected leads to" value="" onChange={(e) => e.target.value && confirmThen("owner", e.target.value === "__none" ? "" : e.target.value,
          e.target.value === "__none" ? `Remove the owner from ${picked.length} lead(s)?` : `Reassign ${picked.length} lead(s) to ${e.target.value}?`, "Yes, assign")} style={{ width: "auto" }}>
          <option value="">Assign to…</option>
          {owners.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value="__none">Nobody (unassign)</option>
        </select>
        <select aria-label="Add selected leads to a list" value="" onChange={(e) => e.target.value && confirmThen("list_add", e.target.value,
          `Add ${picked.length} lead(s) to “${lists.find((l) => l.id === e.target.value)?.name || "this list"}”?`, "Yes, add them")} style={{ width: "auto" }}>
          <option value="">Add to list…</option>
          {lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <span className="row" style={{ gap: 4 }}>
          <input aria-label="Tag for selected leads" placeholder="Tag" maxLength={40} value={tagText} onChange={(e) => setTagText(e.target.value)} style={{ width: 110 }} />
          <button className="btn ghost" disabled={!tagText.trim() || busy === "tag_add"} onClick={() => tagThen("tag_add")}>Add tag</button>
          <button className="btn ghost" disabled={!tagText.trim() || busy === "tag_remove"} onClick={() => tagThen("tag_remove")}>Remove tag</button>
        </span>
        <button className="btn ghost danger" onClick={() => confirmThen("do_not_contact", null, "Put on the do-not-contact list?", "Yes, never contact them", true)}>Do not contact</button>
        {isOwner() && <button className="btn ghost danger" onClick={() => confirmThen("delete", null, "Delete these leads?", `Yes, delete ${picked.length}`, true)}>Delete</button>}
        {isOwner() && spaces.length > 1 && <select aria-label="Move selected leads to a workspace" value="" disabled={busy === "ws"} onChange={(e) => e.target.value && moveTo(e.target.value)} style={{ width: "auto" }}>
          <option value="">Move to workspace…</option>{spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>}
      </>}
      {isOwner() && <DownloadButton path={exportPath("leads")} body={{ ids: picked }} label="Download CSV" testId="download-leads-picked" />}
      <button className="btn ghost" onClick={onClear}>Clear</button>
      {ask && (
        <ConfirmDialog title={ask.title} confirmLabel={ask.label} danger={ask.danger} busy={Boolean(busy)}
          onCancel={() => { setAsk(null); setForce(false); }} onConfirm={() => (ask.action === "workspace" ? doMove(ask.value) : run(ask.action, ask.value))}>
          <CountLine picked={picked} pageIds={pageIds} />
          {ask.action === "enroll" && <p style={{ margin: 0 }}>Leads with no email, already in the sequence, unsubscribed or on the do-not-contact list are skipped. Sending still follows your daily cap, business hours and practice mode.</p>}
          {ask.action === "stage" && <p style={{ margin: 0 }}>Leads that can’t make this move (for example Interested without recorded consent) are left as they are.</p>}
          {ask.action === "stage" && isOwner() && <label className="check-row"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Skip the normal step order (owner). Consent and unsubscribes are still respected.</label>}
          {["owner", "stage", "list_add"].includes(ask.action) && <p style={{ margin: 0 }}>You can undo this for 24 hours. Leads changed by someone else in the meantime are not touched by the undo.</p>}
          {ask.action === "list_add" && <p style={{ margin: 0 }}>Leads already in the list or in another workspace are left out.</p>}
          {ask.action === "do_not_contact" && <p style={{ margin: 0 }}>Their emails go on the suppression list and any running email sequence stops. This is not undone here.</p>}
          {ask.action === "delete" && <p style={{ margin: 0 }}>Deleted leads are gone for everyone; the deletion is written to the Change log.</p>}
          {ask.action === "workspace" && <p style={{ margin: 0 }}>It is logged on each lead.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
