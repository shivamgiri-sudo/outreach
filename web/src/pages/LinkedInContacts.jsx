import { useEffect, useState } from "react";
import { api } from "../api.js";

// Who in this lead's workspace is connected to, or has contacted, this LinkedIn person (app/linkedin_dupes.contacts).
// Other workspaces never appear: the server only returns the lead's own workspace.
export default function LinkedInContacts({ lead }) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let live = true;
    if (!lead?.linkedin_url) { setRows([]); return undefined; }
    api.linkedinContacts(lead.id).then((r) => { if (live) setRows(r.contacts || []); }).catch(() => { if (live) setRows([]); });
    return () => { live = false; };
  }, [lead?.id, lead?.linkedin_url, lead?.linkedin_seq?.status, lead?.linkedin_seq?.last_sent_at]);
  if (!rows.length) return null;
  return (
    <div className="card" data-testid="li-contacts" style={{ margin: "8px 0" }}>
      <b>Who has contacted this person on LinkedIn</b>
      <table className="table" style={{ marginTop: 6 }}>
        <thead><tr><th>Teammate</th><th>Workspace</th><th>LinkedIn ID</th><th>Status</th><th>Sent</th><th>Connected</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.lead_id}>
            <td data-label="Teammate">{r.user || "—"}{r.this_lead && <span className="pill" style={{ marginLeft: 6 }}>this lead</span>}</td>
            <td data-label="Workspace">{r.workspace}</td>
            <td data-label="LinkedIn ID">{r.linkedin_account || "—"}</td>
            <td data-label="Status">{r.status}</td>
            <td data-label="Sent">{r.sent_on || "—"}</td>
            <td data-label="Connected">{r.connected_on || "—"}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}
