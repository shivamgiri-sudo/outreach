import { useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";

// "Why not sent?": the ordered reasons nothing has gone to this person, and what the agent really sent (app/linkedin_why.py).
const COLOUR = { block: "#b91c1c", wait: "#b45309", info: "inherit" };

export default function LinkedInWhy({ lead }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  async function open() {
    setErr("");
    try { setData(await api.linkedinWhy(lead.id)); } catch (e) { setErr(e.message || "Could not load."); }
  }
  if (!lead.linkedin_seq && !lead.linkedin_url) return null;
  return (
    <div style={{ margin: "6px 0" }} data-testid="li-why">
      <button className="btn ghost" onClick={open}>{data ? "Refresh: why not sent?" : "Why not sent?"}</button>
      {err && <span className="sub" style={{ color: "#b91c1c", marginLeft: 8 }}>{err}</span>}
      {data && (
        <div className="card" style={{ marginTop: 6 }}>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {data.reasons.map((r, i) => <li key={i} style={{ color: COLOUR[r.severity] }}>{r.text}</li>)}
          </ul>
          {data.sends.length > 0 && <>
            <b>Sent so far</b>
            {data.sends.map((s, i) => (
              <details key={i} style={{ borderLeft: s.mismatch ? "4px solid #b91c1c" : "none", paddingLeft: 6 }}>
                <summary>Step {s.step} to {s.to} {s.mismatch ? "- WRONG PERSON" : s.unverified ? "- page not verified" : "- verified"} ({istLabel(s.at)})</summary>
                {s.observed_name && <div className="sub">Agent saw: {s.observed_name} {s.observed_url}{s.chat_title ? `, chat "${s.chat_title}"` : ""}</div>}
                {s.mismatch_why && <div className="sub" style={{ color: "#b91c1c" }}>{s.mismatch_why}</div>}
                <pre style={{ whiteSpace: "pre-wrap" }}>{s.text_sent || "(the agent did not report the text)"}</pre>
              </details>))}
          </>}
        </div>)}
    </div>
  );
}
