import { useEffect, useState } from "react";
import { api } from "../api.js";
import { stageLabel } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";

// "Other people at this company": contacts sharing this lead's business domain (or company name). Click to open one.
export default function OtherContacts({ lead }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => { setData(null); api.companyOfLead(lead.id).then(setData).catch(() => setData({ others: [] })); }, [lead.id]);
  if (!data || !data.others?.length) return null;
  const shown = data.others.slice(0, 12);
  return (
    <section className="card" style={{ margin: "12px 0" }} aria-label="Other people at this company" data-testid="other-contacts">
      <b>Other people at {data.name || "this company"} ({data.others.length})</b>
      {data.others.some((o) => o.in_sequence) && <p className="sub" style={{ margin: "4px 0" }}>Someone else here is already in an email sequence — avoid contacting two people on the same day.</p>}
      <div>
        {shown.map((o) => (
          <div key={o.id} className="row" style={{ justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--line)", gap: 6, flexWrap: "wrap" }}>
            <span style={{ minWidth: 0 }}>
              <button className="linkish" onClick={() => setOpen(o.id)}>{o.contact_name || o.email || "Unnamed contact"}</button>
              {o.title && <small className="sub-inline"> · {o.title}</small>}
            </span>
            <span className="chips" style={{ margin: 0 }}>
              <span className="pill">{stageLabel(o.stage)}</span>
              {o.in_sequence && <span className="pill rec">In sequence</span>}
            </span>
          </div>
        ))}
        {data.others.length > shown.length && <small className="sub">and {data.others.length - shown.length} more — see Companies.</small>}
      </div>
      {open && <LeadDrawer id={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
