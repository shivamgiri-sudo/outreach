// Lead drawer: reaching the right person (Overview tab) and the domain trust row (Data tab).
//   FindTheRightPerson  "Ask who handles hiring" (drafts a ready email into the one-off composer; the person presses Send)
//                       and "Search the web" links that open in a new tab for a person to review (nothing is fetched).
//   DomainSignals       mail records + domain age from public DNS / RDAP (app/domain_signals.py), with a Check button.
import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { istLabel } from "../time.js";
import { Badge, Button, Card, CardHeader } from "../ui/index.js";
import { Composer } from "./LeadActivity.jsx";
import { domainParts, isYoungDomain, noMailAuth } from "./domainSignals.js";

function useResearch(lead) {
  const [data, setData] = useState(null);
  useEffect(() => { setData(null); api.leadResearch(lead.id).then(setData).catch(() => setData({ links: [], ask: { offer: false } })); }, [lead.id, lead.email, lead.contact_name, lead.stage]);
  return [data, setData];
}

export function FindTheRightPerson({ lead, onChanged }) {
  const [data] = useResearch(lead);
  const [drafting, setDrafting] = useState(false);
  const [done, setDone] = useState("");
  if (!data || (!data.links?.length && !data.ask?.offer)) return null;
  const ask = data.ask || {};
  return (
    <Card flat className="dsec" aria-label="Find the right person" data-testid="find-right-person">
      <CardHeader title="Find the right person" />
      {ask.offer && canWrite() && !drafting && (
        <div className="research-ask">
          <p className="sub">This lead has only a shared address or no named person. Ask them who handles hiring, then write to that person.</p>
          <Button variant="secondary" size="sm" onClick={() => { setDone(""); setDrafting(true); }} data-testid="ask-who-hires">Ask who handles hiring</Button>
        </div>
      )}
      {drafting && (
        <Composer lead={lead} initialSubject={ask.subject} initialBody={ask.body}
          note={[ask.gate_note, "A ready template: read it, change anything you like, then press Send. Nothing is sent until you do."].filter(Boolean).join(" ")}
          onCancel={() => setDrafting(false)} onDone={(m) => { setDrafting(false); setDone(m); onChanged?.(); }} />
      )}
      {done && <p className="sub" role="status">{done}</p>}
      {data.links?.length > 0 && (
        <div className="research-links">
          <small className="sub">Search the web (opens in a new tab; check what you find before using it)</small>
          <div className="chips">
            {data.links.map((l) => <a key={l.id} className="btn ghost sm" href={l.url} target="_blank" rel="noopener noreferrer" data-testid={`search-${l.id}`}>{l.label}</a>)}
          </div>
        </div>
      )}
    </Card>
  );
}

export function DomainSignals({ lead, onChanged }) {
  const [data, setData] = useResearch(lead);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!lead.domain || !data) return null;
  const s = data.domain_signals;
  const check = () => {
    setBusy(true); setError("");
    api.checkDomainSignals(lead.id).then((r) => { if (!r.known) setError("The public records could not be read just now. Try again later."); else { setData({ ...data, domain_signals: r.domain_signals }); onChanged?.(); } })
      .catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  const risky = Boolean(s) && (isYoungDomain({ domain_signals: s }) || noMailAuth({ domain_signals: s }));
  return (
    <section className="card u-m-12-0" aria-label="Domain trust" data-testid="domain-signals">
      <div className="row u-between u-wrap">
        <b>Domain: {lead.domain} {risky && <Badge tone="warning" className="u-ml-8">check before emailing</Badge>}</b>
        {canWrite() && <Button variant="secondary" size="sm" onClick={check} disabled={busy}>{busy ? "Checking…" : s ? "Check again" : "Check domain"}</Button>}
      </div>
      {s ? (
        <ul className="domain-row" aria-label="Mail records and domain age">
          {domainParts(s).map((p) => <li key={p.label} className={p.warn ? "domain-warn" : undefined}><small className="sub">{p.label}</small> {p.text}</li>)}
        </ul>
      ) : <p className="sub">Not checked yet. Reads the domain's public mail records (MX, SPF, DMARC) and registration date. Takes a few seconds.</p>}
      {(risky || !s) && <p className="sub">{data.why}</p>}
      {s?.checked_at && <small className="sub">Checked {istLabel(s.checked_at)} from public DNS and RDAP.</small>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
