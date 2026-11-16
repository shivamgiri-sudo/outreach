import { useState } from "react";
import { canWrite } from "../api.js";
import { ops } from "../opsApi.js";
import { istDateLabel, istLabel } from "../time.js";
import { Badge, Button } from "../ui/index.js";
import HiringChip from "./HiringChip.jsx";

const FACT_LABELS = { name: "Name", city: "City", address: "Address", phone: "Phone", logo_url: "Logo", company_linkedin_url: "LinkedIn" };
const SOCIAL = { x: "X", facebook: "Facebook", instagram: "Instagram", youtube: "YouTube" };
const shortUrl = (u) => { try { const x = new URL(u); return x.hostname + x.pathname; } catch { return u; } };
const link = (url, text) => <a href={url} target="_blank" rel="noopener noreferrer">{text}</a>;

// Hiring now (jobs the company publishes on its own website) and the facts its pages state about it.
export default function HiringSignal({ lead, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const h = lead.hiring_signal;
  const facts = lead.company_facts?.found || {};
  const check = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await ops.hiringSignalRefresh(lead.id);
      setMsg(r.error ? `The website could not be read fully: ${r.error}` : r.filled.length ? `Filled from the website: ${r.filled.join(", ").replace(/_/g, " ")}.` : "");
      onChanged?.();
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  return (
    <section className="card u-m-12-0" aria-label="Hiring now" data-testid="hiring-signal">
      <div className="row u-between u-wrap">
        <b>Hiring now <HiringChip lead={lead} /></b>
        {canWrite() && lead.domain && <Button variant="secondary" size="sm" onClick={check} disabled={busy}>{busy ? "Reading the website…" : h ? "Check again" : "Check careers page"}</Button>}
      </div>
      {!h && <p className="sub">{lead.domain ? `Reads the jobs ${lead.domain} publishes on its own careers page (the markup Google for Jobs uses) and the company details on its home page. Takes up to a minute.` : "This lead has no website, so there is nothing to read."}</p>}
      {msg && <p className="sub" role="status">{msg}</p>}
      {h && (
        <>
          {h.open_roles > 0 ? (
            <details className="hiring-roles">
              <summary>{h.open_roles} open role(s){h.latest_posted ? `, newest posted ${istDateLabel(h.latest_posted)}` : ""}</summary>
              <ul className="sub">{h.titles.map((t) => <li key={t}>{t}</li>)}</ul>
              {h.locations.length > 0 && <small className="sub">Places: {h.locations.join(", ")}</small>}
            </details>
          ) : <p className="sub">No open roles published on the website.{h.note ? ` ${h.note}` : ""}</p>}
          {h.sources.length > 0 && <small className="sub hiring-sources">Found on: {h.sources.map((u, i) => <span key={u}>{i ? ", " : ""}{link(u, shortUrl(u))}</span>)}</small>}
          {Object.keys(facts).length > 0 && (
            <div className="hiring-facts">
              <small className="sub">The website says about the company</small>
              <div className="pill-stack">
                {Object.entries(FACT_LABELS).filter(([k]) => facts[k]).map(([k, label]) => (
                  <Badge key={k} tone={(lead.company_facts.filled || []).includes(k === "name" ? "company" : k) ? "success" : "neutral"}>
                    {label}: {k === "logo_url" || k === "company_linkedin_url" ? link(facts[k], "open") : facts[k]}
                  </Badge>))}
                {Object.entries(facts.social || {}).map(([k, u]) => <Badge key={k}>{link(u, SOCIAL[k] || k)}</Badge>)}
              </div>
              <small className="sub">Green ones filled an empty field on this lead (source: site markup). Fields you already had are never changed.</small>
            </div>
          )}
          <small className="sub">Checked {istLabel(h.checked_at)}.</small>
        </>
      )}
    </section>
  );
}
