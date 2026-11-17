import { useEffect, useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";
import { Badge } from "../ui/index.js";

// "Where this came from": every filled field with its source, date and type, read only (GET /api/leads/{id}/sources,
// built by app/lead_sources_view.py). Loads when opened. "Not recorded" means the app kept no record for that value.
const KIND = { found: ["info", "Found"], guessed: ["warning", "Guessed"], confirmed: ["success", "Confirmed"], typed: ["neutral", "Typed by a person"] };
const BASIS = { history: "first mentioned in the history", edit: "edited", "register match": "matched in the register" };
const isUrl = (v) => /^https?:\/\//i.test(v || "");

function Row({ r }) {
  const [tone, word] = KIND[r.kind] || ["muted", "Not recorded"];
  return (
    <li className="src-row" data-testid="source-row" data-field={r.field}>
      <div className="src-head"><b>{r.label}</b> <Badge tone={tone}>{word}</Badge></div>
      <div className="src-value" title={r.value}>{r.value}</div>
      <small className="sub src-meta">
        {r.source_label === "not recorded" ? "Source not recorded" : `From: ${r.source_label}`}
        {r.detail && (isUrl(r.detail) ? <> · <a href={r.detail} target="_blank" rel="noopener noreferrer">page</a></> : ` · ${r.detail}`)}
        {r.by && ` · by ${r.by}`}
        {r.at ? ` · ${istLabel(r.at)}${BASIS[r.at_basis] ? ` (${BASIS[r.at_basis]})` : ""}` : " · date not recorded"}
        {r.check && r.check.status && ` · check: ${r.check.status.replace(/_/g, " ")}${r.check.provider ? ` (${r.check.provider})` : ""}${r.check.at ? `, ${istLabel(r.check.at)}` : ""}`}
      </small>
    </li>
  );
}

export default function LeadSources({ lead }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { setData(null); setError(""); }, [lead.id]);
  useEffect(() => { if (open && !data) api.leadValueSources(lead.id).then(setData).catch((e) => setError(e.message)); }, [open, data, lead.id]);
  const o = data?.origin;
  return (
    <details className="card dsec lead-sources" data-testid="lead-sources" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="card-summary">Where this came from</summary>
      {error && <div className="tip error" role="alert">{error}</div>}
      {open && !data && !error && <p className="sub">Loading...</p>}
      {data && (
        <>
          <p className="sub">
            First added from <b>{o.source || "an unknown source"}</b>{o.at ? ` on ${istLabel(o.at)}` : ""}{o.detail ? ` (${o.detail})` : ""}.
            {o.source_url && <> Found on <a href={o.source_url} target="_blank" rel="noopener noreferrer">this page</a>.</>}
            {data.not_recorded > 0 && ` ${data.not_recorded} value(s) have no record of their own; they most likely came with that first source.`}
          </p>
          {data.fields.length ? <ul className="src-list">{data.fields.map((r) => <Row key={r.field} r={r} />)}</ul> : <p className="sub">No details filled in yet.</p>}
        </>
      )}
    </details>
  );
}
