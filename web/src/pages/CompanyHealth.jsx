import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { istDateLabel, istLabel } from "../time.js";

const BAND = { strong: "ok", steady: "", "weak / unknown": "warn" };

// Company health from free public data: website, DNS email setup, domain age, Wikidata, news, plus our own hiring data.
export default function CompanyHealth({ lead }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.companyHealth(lead.id).then(setData).catch(() => setData({ report: null })); }, [lead.id]);
  const build = () => { setBusy(true); setError(""); api.refreshCompanyHealth(lead.id).then(setData).catch((e) => setError(e.message)).finally(() => setBusy(false)); };
  const r = data?.report;
  return (
    <section className="card" style={{ margin: "12px 0" }} aria-label="Company health" data-testid="company-health">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <b>Company health {r && <span className={`pill ${BAND[r.band] || ""}`} style={{ marginLeft: 6 }}>{r.score}/100 · {r.band}</span>}</b>
        {canWrite() && <button className="btn ghost" onClick={build} disabled={busy}>{busy ? "Checking public sources…" : r ? "Refresh" : "Build health report"}</button>}
      </div>
      {!r && !busy && <p className="sub">{canWrite() ? "" : "No health report yet. "}Checks free public sources for {data?.domain || "the company's domain"}: its website, email setup, domain age, Wikidata and recent news. Takes about 10–20 seconds.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {r && (
        <>
          <ul className="sub" style={{ margin: "8px 0", paddingLeft: 18 }}>
            {r.reasons.map((x, i) => <li key={i}><b style={{ color: x.points < 0 ? "var(--accent)" : "inherit" }}>{x.points > 0 ? "+" : ""}{x.points}</b> {x.why}</li>)}
          </ul>
          <div className="grid g2" style={{ gap: 10 }}>
            <div><small className="sub">Website</small><div>{r.website.ok ? <a href={r.website.url} target="_blank" rel="noopener noreferrer">{r.website.title || r.domain}</a> : "not reachable"}</div>
              {r.website.description && <small className="sub">{r.website.description.slice(0, 160)}</small>}
              {r.website.tools?.length > 0 && <div><small className="sub">Tools seen: {r.website.tools.join(", ")}</small></div>}
              {r.website.social && Object.keys(r.website.social).length > 0 && <div><small>{Object.entries(r.website.social).map(([k, u]) => <a key={k} href={u} target="_blank" rel="noopener noreferrer" style={{ marginRight: 8 }}>{k}</a>)}</small></div>}</div>
            <div><small className="sub">Email setup</small><div>{r.email.provider || "no mail server found"}</div>
              <small className="sub">SPF {r.email.spf ? "yes" : "no"} · DMARC {r.email.dmarc ? (r.email.dmarc_policy || "yes") : "no"}</small></div>
            <div><small className="sub">Domain age</small><div>{r.domain_age.ok ? `${r.domain_age.years} years (since ${r.domain_age.registered})` : "not published"}</div></div>
            <div><small className="sub">Public profile</small><div>{r.wikidata.ok ? <a href={r.wikidata.url} target="_blank" rel="noopener noreferrer">{r.wikidata.label}</a> : "not on Wikidata"}</div>
              {r.wikidata.ok && <small className="sub">{[r.wikidata.description, r.wikidata.founded && `founded ${r.wikidata.founded}`, r.wikidata.employees && `~${r.wikidata.employees} employees`].filter(Boolean).join(" · ")}</small>}</div>
          </div>
          <div style={{ marginTop: 10 }}><small className="sub">News, last 6 months ({r.news.ok ? `${r.news.count} headlines` : "not available"})</small>
            {r.news.ok && r.news.items.map((n, i) => (
              <div key={i}><small>{n.signal && <span className={`pill ${n.signal === "risk" ? "warn" : "ok"}`} style={{ marginRight: 4 }}>{n.signal}</span>}
                <a href={n.url} target="_blank" rel="noopener noreferrer">{n.title}</a> <span className="sub">{istDateLabel(n.date)}</span></small></div>))}
          </div>
          <small className="sub">Built {istLabel(r.built_at)} from {r.sources.join(", ")}. A guide for prioritising, not a credit rating.</small>
        </>
      )}
    </section>
  );
}
