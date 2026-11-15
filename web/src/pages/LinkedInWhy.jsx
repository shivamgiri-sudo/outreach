import { useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";

// "Why not sent?": the ordered reasons nothing has gone to this person, and what the agent really sent (app/linkedin_why.py).
const SEVERITY = { block: "li-block", wait: "li-wait", info: "" };

export default function LinkedInWhy({ lead }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  async function open() {
    setErr("");
    try { setData(await api.linkedinWhy(lead.id)); } catch (e) { setErr(e.message || "Could not load."); }
  }
  if (!lead.linkedin_seq && !lead.linkedin_url) return null;
  return (
    <div className="u-m-8-0" data-testid="li-why">
      <button className="btn ghost" onClick={open}>{data ? "Refresh: why not sent?" : "Why not sent?"}</button>
      {err && <span className="sub li-block u-ml-8">{err}</span>}
      {data && (
        <div className="card u-mt-8">
          <ul className="li-reasons">
            {data.reasons.map((r, i) => <li key={i} className={SEVERITY[r.severity]}>{r.text}</li>)}
          </ul>
          {data.sends.length > 0 && <>
            <b>Sent so far</b>
            {data.sends.map((s, i) => (
              <details key={i} className={s.mismatch ? "li-mismatch" : undefined}>
                <summary>Step {s.step} to {s.to} {s.mismatch ? "- WRONG PERSON" : s.unverified ? "- page not verified" : "- verified"} ({istLabel(s.at)})</summary>
                {s.observed_name && <div className="sub">Agent saw: {s.observed_name} {s.observed_url}{s.chat_title ? `, chat "${s.chat_title}"` : ""}</div>}
                {s.mismatch_why && <div className="sub li-block">{s.mismatch_why}</div>}
                <pre className="u-prewrap">{s.text_sent || "(the agent did not report the text)"}</pre>
              </details>))}
          </>}
        </div>)}
    </div>
  );
}
