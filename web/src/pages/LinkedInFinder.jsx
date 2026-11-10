import { useState } from "react";
import { api, canWrite } from "../api.js";

// A lead with no LinkedIn link whose profile is being looked up (app/linkedin_finder.py): "Finding profile" while the agent
// has not searched yet, the people it found with a one-click "This is them", or a prefilled LinkedIn search plus a field to
// paste the link. `row` is {lead_id, name, company, status, candidates, search_url}; a lead's own linkedin_lookup works too.
export const LOOKUP_WORDS = { queued: "Finding profile", ambiguous: "Pick the right person", not_found: "Profile not found", manual: "Profile needed" };

export default function LinkedInFinder({ row, onChanged, onMessage }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const say = (m) => (onMessage ? onMessage(m) : null);
  const send = async (action, link, done) => {
    setBusy(true);
    try { await api.linkedinFind(row.lead_id, action, link); say(done); setUrl(""); onChanged && onChanged(); } catch (e) { say(e.message); }
    setBusy(false);
  };
  const cands = row.candidates || [];
  return (
    <div data-testid="li-finder" data-status={row.status}>
      <span className="pill warn">{LOOKUP_WORDS[row.status] || "Finding profile"}</span>{" "}
      {row.status === "queued" && <span className="sub">The LinkedIn agent will search for “{row.name} {row.company}” on its next free turn (a few a day). Nothing is sent until the right profile is found.</span>}
      {row.status === "ambiguous" && <span className="sub">The agent found more than one possible person, so nothing was chosen for you.</span>}
      {(row.status === "not_found" || row.status === "manual") && <span className="sub">Nobody matched automatically.</span>}
      {cands.length > 0 && <ul className="task-list" style={{ marginTop: 6 }}>{cands.map((c) => (
        <li key={c.url}><div><b>{c.name}</b>{c.headline ? ` · ${c.headline}` : ""}{c.location ? ` · ${c.location}` : ""}</div>
          <div className="row"><a className="btn ghost" href={c.url} target="_blank" rel="noopener noreferrer">Open profile</a>
            {canWrite() && <button className="btn" disabled={busy} onClick={() => send("pick", c.url, "Profile chosen. The LinkedIn sequence has started.")}>This is them</button>}</div></li>))}
      </ul>}
      {row.status !== "queued" && row.search_url && <p style={{ margin: "6px 0" }}><a href={row.search_url} target="_blank" rel="noopener noreferrer">Search on LinkedIn</a> for “{row.name} {row.company}”</p>}
      {canWrite() && <div className="row" style={{ marginTop: 4 }}>
        <input aria-label={`LinkedIn profile link for ${row.name}`} placeholder="Paste their LinkedIn profile link" value={url} onChange={(e) => setUrl(e.target.value)} style={{ minWidth: 220, flex: 1 }} />
        <button className="btn ghost" disabled={busy || !url.trim()} onClick={() => send("link", url.trim(), "Link saved. The LinkedIn sequence has started.")}>Use this link</button>
        <button className="btn ghost" disabled={busy} onClick={() => send("skip", "", "Skipped. Add the link on the lead when you have it.")}>Skip</button>
      </div>}
    </div>
  );
}
