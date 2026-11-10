import { useState } from "react";
import { api, canWrite } from "../api.js";
import { useConfirm } from "../Dialog.jsx";
import { whenItSends } from "./LinkedInStatus.jsx";

// Lead drawer: start LinkedIn outreach for this one person, or stop it. After a start it says WHEN the agent will send
// (it works one action at a time inside working hours), so nobody expects the LinkedIn window to move at once.
const ENDED = new Set(["finished", "stopped", "replied"]);
const LOOKING = new Set(["queued", "ambiguous", "not_found", "manual"]);

export default function LinkedInStartStop({ lead, onLead, say }) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  if (!canWrite()) return null;
  const seq = lead.linkedin_seq;
  const lookingUp = !seq && lead.linkedin_lookup && LOOKING.has(lead.linkedin_lookup.status) && !lead.linkedin_lookup.skipped_by;
  const run = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { say(e.message); } finally { setBusy(false); }
  };
  const start = () => run(async () => {
    const r = await api.linkedinStart(lead.id);
    onLead(await api.lead(lead.id).catch(() => r.lead));
    if (r.lookup) {
      say("This person has no LinkedIn link yet. The agent first searches LinkedIn once for their name and company and starts the "
        + "sequence only if exactly one profile matches; otherwise it asks you to pick. You can also paste their profile link in Overview.");
      return;
    }
    say(`On the LinkedIn sequence. ${whenItSends(await api.linkedinAgent().catch(() => null))}`);
  });
  const stop = () => run(async () => {
    if (!(await confirm(`Stop LinkedIn outreach for ${lead.contact_name || "this person"}? Nothing more is sent to them on LinkedIn; it cannot be restarted.`))) return;
    onLead(await api.linkedinStop(lead.id));
    say("LinkedIn outreach stopped for this person.");
  });
  if (seq && !ENDED.has(seq.status)) {
    return <div className="row" style={{ marginBottom: 8 }}><button type="button" className="btn ghost" disabled={busy} data-testid="drawer-li-stop" onClick={stop}>{busy ? "Working…" : "Stop LinkedIn outreach"}</button></div>;
  }
  if (seq || lead.linkedin_dup_of || lookingUp) return null;
  return (
    <div className="row" style={{ marginBottom: 8 }}>
      <button type="button" className="btn" disabled={busy} data-testid="drawer-li-start" onClick={start}>{busy ? "Starting…" : "Start LinkedIn outreach"}</button>
      {!/linkedin\.com\/(in|pub)\//i.test(lead.linkedin_url || "") && <small className="sub">No LinkedIn link yet: the agent looks the profile up first.</small>}
    </div>
  );
}
