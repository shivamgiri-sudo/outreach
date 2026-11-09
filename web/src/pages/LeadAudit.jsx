import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";
import DownloadButton from "../DownloadButton.jsx";

// The lead's consent record (every yes / opt-out / do-not-contact, and where each channel stands now) and,
// for owners, the audit pack download for this one lead.
const STATUS = {
  opted_in: ["ok", "Opted in"], opted_out: ["err", "Opted out"], do_not_contact: ["err", "Do not contact"],
  not_allowed: ["warn", "Not allowed"], no_consent: ["", "No consent yet"],
};
const EVENT = { given: "Said yes", withdrawn: "Opted out", do_not_contact: "Do-not-contact" };
const KIND = { reply: "their reply", form: "a form", person: "a person", import: "an import", link: "unsubscribe link",
  provider: "email provider", erasure: "erasure request", system: "the system" };

export function LeadConsent({ lead }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError("");
    api.leadConsent(lead.id).then(setData).catch((e) => setError(e.message));
  }, [lead.id, lead.consent, lead.stage, lead.do_not_contact]);
  return (
    <details className="card" style={{ padding: 10, margin: "10px 0" }} data-testid="lead-consent">
      <summary><b>Consent</b>{data && <small className="sub-inline"> · {Object.values(data.status).filter((s) => s.status === "opted_in").map((s) => s.label).join(", ") || "no channel opted in"}</small>}</summary>
      {error && <div className="tip error" role="alert">{error}</div>}
      {!data ? !error && <p className="empty">Loading…</p> : (
        <>
          <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
            {Object.values(data.status).map((s) => (
              <span key={s.channel} className={`pill ${STATUS[s.status]?.[0] || ""}`} title={s.why + (s.since ? ` (${istLabel(s.since)})` : "")}>
                {s.label}: {STATUS[s.status]?.[1] || s.status}
              </span>
            ))}
          </div>
          {data.events.length === 0 ? <p className="sub">No consent events recorded yet.</p> : (
            <div className="timeline">
              {data.events.map((e, i) => (
                <div key={i}><small>{istLabel(e.at)} · {EVENT[e.event] || e.event} · {e.channel} · from {KIND[e.source_kind] || e.source_kind}{e.by ? ` (${e.by})` : ""}</small>
                  {e.source}{e.text && e.text !== e.source ? <>: <i>{e.text}</i></> : null}</div>
              ))}
            </div>
          )}
        </>
      )}
    </details>
  );
}

export function LeadAuditPack({ lead }) {
  if (!isOwner()) return null;
  return (
    <p className="sub" style={{ marginTop: 12 }}>
      <DownloadButton path={ops.auditPackPath({ lead_id: lead.id })} label="Download audit pack" filename={`audit-pack-${lead.id}.zip`} testId="lead-audit-pack" />
      {" "}Messages, change log, consent events and the change-log check for this lead (zip of CSVs).
    </p>
  );
}
