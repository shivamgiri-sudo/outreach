// Small building blocks of the Lists page: the company picker, the "enriched leads ready" banner and the journey chips.
import { useEffect, useMemo, useState } from "react";
import { api, canWrite } from "../api.js";
import { Button, Card, CardHeader, Input } from "../ui/index.js";

export const result = (verb, r) => `${verb} for ${r.started} of ${r.asked}.${r.refused.length ? " Left out: " + r.refused.map((x) => `${x.count} (${x.reason})`).join("; ") : ""}`;

// Step chips for one lead: how far outreach got.
export function Journey({ j }) {
  const steps = [["Email started", j.email_started], ["Emailed", j.emailed], ["Opened", j.opened], ["Replied", j.replied],
    ["LinkedIn started", j.linkedin_started], ["Request sent", j.linkedin_sent], ["Accepted", j.linkedin_accepted], ["Interested", j.interested]];
  return <span className="chips">{steps.filter(([, on]) => on).map(([l]) => <span className="pill ok" key={l}>{l}</span>)}{!steps.some(([, on]) => on) && <span className="sub-inline">Not started</span>}</span>;
}

export function CompanyPicker({ onCreated }) {
  const [companies, setCompanies] = useState([]);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState([]);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.listCompanies().then(setCompanies).catch((e) => setMsg(e.message)); }, []);
  const shown = useMemo(() => companies.filter((c) => c.company.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 200), [companies, q]);
  const toggle = (c) => setPicked((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
  const create = async () => {
    setMsg("");
    try { const r = await api.createList({ name: name || `${picked.length} companies`, companies: picked }); setPicked([]); setName(""); onCreated(r); } catch (e) { setMsg(e.message); }
  };
  return (
    <Card className="list-picker">
      <CardHeader title="Choose companies" description="Tick the companies you want to reach. Only they are put in the list; nobody else gets an email or a LinkedIn request." />
      <Input icon="search" aria-label="Search companies" placeholder="Search companies" value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} />
      <div className="picklist" role="group" aria-label="Companies">
        {shown.length === 0 && <p className="empty">No companies yet.</p>}
        {shown.map((c) => (
          <label key={c.company} className="pick-row">
            <input type="checkbox" checked={picked.includes(c.company)} onChange={() => toggle(c.company)} />
            <span className="pick-text"><b title={c.company}>{c.company}</b>
              <small className="sub-inline">{c.leads} lead{c.leads === 1 ? "" : "s"} · {c.with_email} email · {c.with_linkedin} LinkedIn{c.enriched ? ` · ${c.enriched} enriched` : ""}</small></span>
          </label>
        ))}
      </div>
      <div className="list-create">
        <Input aria-label="List name" placeholder="Name this list (e.g. Noida BPOs, Nov)" value={name} onChange={(e) => setName(e.target.value)} />
        <Button disabled={!picked.length} onClick={create}>Create list from {picked.length} compan{picked.length === 1 ? "y" : "ies"}</Button>
      </div>
      {msg && <div className="tip error" role="alert">{msg}</div>}
    </Card>
  );
}

export function EnrichedBanner({ onDone }) {
  const [s, setS] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.enrichedSummary().then(setS).catch(() => {}); }, []);
  if (!s || !s.enriched) return null;
  const go = async () => {
    setBusy(true); setMsg("");
    try {
      const list = await api.createList({ name: `Enriched ${new Date().toISOString().slice(0, 10)}`, lead_ids: s.ready_ids });
      const r = await api.listStartEmail(list.id);
      setMsg(result("Email campaign started", r)); setConfirm(false); onDone();
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  return (
    <Card className="list-banner">
      <div className="row between">
        <div><b>{s.enriched} enriched leads</b> · <b>{s.email_ready}</b> from {s.companies} companies are ready for email and not started yet.</div>
        {!confirm && canWrite() && <Button disabled={!s.email_ready} onClick={() => setConfirm(true)}>Start email campaigning</Button>}
      </div>
      {confirm && (
        <div className="tip mt-3" role="alert">
          This puts {s.email_ready} leads in a new list and starts their email sequence. Sending still follows your daily cap, business hours and practice mode.
          To email only some of them, create a list from chosen companies below instead.
          <div className="row mt-3">
            <Button disabled={busy} onClick={go}>{busy ? "Starting…" : `Yes, start for ${s.email_ready}`}</Button>
            <Button variant="secondary" onClick={() => setConfirm(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {msg && <p className="sub mt-3" role="status">{msg}</p>}
    </Card>
  );
}

