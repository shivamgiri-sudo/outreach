import { useEffect, useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";

const SKIP_TEXT = { "do-not-contact": "on the do-not-contact list", unsubscribed: "unsubscribed", erased: "number of an erased person" };

// Owner only (the Data hygiene page shows it to owners). Dry run first, then Apply; the last run can be undone.
export default function RestoreLandlines({ onChanged }) {
  const [plan, setPlan] = useState(null);
  const [lastRun, setLastRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const confirm = useConfirm();
  useEffect(() => { api.restoreLandlinesPlan().then((r) => setLastRun(r.last_run || null)).catch(() => {}); }, []);
  const run = async (work, after) => {
    setBusy(true); setError("");
    try { const r = await work(); setLastRun(r.last_run || null); after(r); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const check = () => run(api.restoreLandlinesPlan, (r) => setPlan(r));
  const apply = async () => {
    if (!(await confirm({ message: `Fill the empty phone of ${plan.would_change} lead(s) with the landline already saved on them? Phones that are set are never changed. You can undo this.`, confirmLabel: "Restore landlines" }))) return;
    run(api.restoreLandlines, (r) => { setPlan(null); onChanged(`Restored a landline on ${r.changed} lead(s). They can be called, never WhatsApp/SMS.`); });
  };
  const undo = async () => {
    if (!(await confirm({ message: `Undo the restore of ${lastRun.changed} lead(s)? Phones changed since are left as they are.`, confirmLabel: "Undo restore" }))) return;
    run(() => api.undoRestoreLandlines(lastRun.id), (r) => { setPlan(null); onChanged(`Undid the restore on ${r.reverted} lead(s)${r.kept ? `; ${r.kept} changed since were left as they are` : ""}.`); });
  };
  const skipped = Object.entries(plan?.skipped || {}).filter(([, n]) => n);
  return (
    <>
      <p className="sub" style={{ margin: "0 0 8px" }}>
        Older campaign, Google Places, OpenStreetMap and prospect leads dropped office landlines from the phone field but kept the
        number elsewhere on the lead (company phone, other numbers, a custom phone field). This puts a landline with its STD code back
        as the phone, only where the phone is empty. Landlines can be called, never WhatsApp or SMS.
      </p>
      {error && <p role="alert" className="tip error">{error}</p>}
      <div className="row">
        {!plan && <button className="btn ghost" disabled={busy} onClick={check} data-testid="landline-check">{busy ? "Checking…" : "Check for landlines to restore"}</button>}
        {lastRun && <button className="btn ghost" disabled={busy} onClick={undo} data-testid="landline-undo">Undo last restore ({lastRun.changed} lead(s), {istLabel(lastRun.at)})</button>}
      </div>
      {plan && (
        <div data-testid="landline-plan">
          <p><b>{plan.would_change}</b> lead(s) would get a landline back.
            {skipped.length > 0 && ` Skipped: ${skipped.map(([k, n]) => `${n} ${SKIP_TEXT[k] || k}`).join(", ")}.`}</p>
          {plan.sample.length > 0 && (
            <ul>
              {plan.sample.map((c) => (
                <li key={c.lead_id} style={{ overflowWrap: "anywhere" }}>
                  <b>{c.company || c.contact_name || c.lead_id}</b>{c.contact_name && c.company ? ` (${c.contact_name})` : ""}: {c.phone} from {c.from.replace(/^custom:/, "custom field ").replace(/_/g, " ")} “{c.raw}”
                </li>
              ))}
            </ul>
          )}
          {plan.would_change > plan.sample.length && <p className="sub">…and {plan.would_change - plan.sample.length} more.</p>}
          <div className="row">
            <button className="btn" disabled={busy || plan.would_change === 0} onClick={apply} data-testid="landline-apply">
              {busy ? "Saving…" : `Restore landlines on ${plan.would_change} lead(s)`}</button>
            <button className="btn ghost" onClick={() => setPlan(null)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  );
}
