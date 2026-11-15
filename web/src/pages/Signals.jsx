import { useEffect, useState } from "react";
import { istDateLabel, istLabel } from "../time.js";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import "../data-pages.css";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Select, Skeleton } from "../ui/index.js";

const KIND_WORDS = { keyword: "Keyword", competitor: "Competitor", company: "Company" };

export default function Signals() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState("keyword");
  const [text, setText] = useState("");
  const [names, setNames] = useState({});
  const [show, setShow] = useState("new");
  const load = () => { setError(""); api.signals().then(setData).catch((e) => setError(e.message)); };
  useEffect(load, []);

  const act = async (fn, ok) => {
    setBusy(true); setMsg("");
    try { const r = await fn(); setMsg(typeof ok === "function" ? ok(r) : ok); load(); }
    catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const items = (data?.items || []).filter((i) => (show === "all" ? true : i.status === show));
  // An article found by watching a company is about that company: start with its name.
  const nameFor = (i) => names[i.id] ?? (i.company || (i.kind === "company" ? i.watch : "") || "");

  return (
    <>
      <PageHeader title="Buying signals"
        description={<>Watch keywords, competitors or companies and see fresh public news about them: hiring drives, new centres, expansion. You choose which company becomes a lead. This reads only the public news feed, never LinkedIn.</>} />
      {error && <ErrorState message="Signals could not load." detail={error} onRetry={load} />}
      <Card as="section">
        {isOwner() && <div className="dp-add">
          <Select aria-label="What to watch" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND_WORDS).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
          </Select>
          <Input aria-label="Words to watch" value={text} maxLength={80} onChange={(e) => setText(e.target.value)}
            placeholder={kind === "keyword" ? "BPO hiring Noida" : kind === "competitor" ? "A competitor's name" : "A company you want to track"}
            onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) act(async () => { await api.addWatch(kind, text.trim()); setText(""); }, "Added."); }} />
          <Button disabled={busy || text.trim().length < 2} onClick={() => act(async () => { await api.addWatch(kind, text.trim()); setText(""); }, "Added.")}>Watch</Button>
          <Button variant="secondary" disabled={busy || !(data?.watch || []).length}
            onClick={() => act(() => api.runSignals(), (r) => `${r.new} new article(s)${r.errors?.length ? `, ${r.errors.length} feed(s) failed` : ""}.`)}>Check now</Button>
        </div>}
        <div className="dp-watch">
          {(data?.watch || []).map((w) => (
            <span key={w.id} className="ui-chip">{KIND_WORDS[w.kind]}: {w.text}
              {isOwner() && <button type="button" aria-label={`Stop watching ${w.text}`} onClick={() => act(() => api.dropWatch(w.id), "Removed.")}>×</button>}</span>
          ))}
          {data && data.watch.length === 0 && <span className="sub u-m0">Nothing watched yet.{isOwner() ? " Add a keyword such as “BPO hiring Noida” above." : ""}</span>}
        </div>
        {data?.last_run && <p className="sub u-mb-0 u-mt-12">Last checked {istLabel(data.last_run)}.</p>}
        {msg && <p role="status" className="sub u-mb-0 u-mt-8">{msg}</p>}
      </Card>
      <div className="dp-toolbar">
        <Select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value)} className="u-w-auto">
          <option value="new">New</option><option value="added">Made into leads</option><option value="dismissed">Dismissed</option><option value="all">All</option>
        </Select>
      </div>
      {!data && !error && <div className="dp-skel u-mt-16" aria-busy="true"><Skeleton height={96} /><Skeleton height={96} /></div>}
      {data && items.length === 0 && <Card className="u-mt-16"><EmptyState icon="zap" title="No articles here">{isOwner() ? "Add something to watch and press Check now. Fresh public news about it shows up here." : "Fresh news about the watched companies will show up here."}</EmptyState></Card>}
      <div className="dp-feed">
      {items.map((i) => (
        <Card key={i.id} className="dp-article">
          <a href={i.link} target="_blank" rel="noopener noreferrer">{i.title}</a>
          <div className="meta"><span>{i.source}{i.published ? ` · ${istDateLabel(i.published)}` : ""} · {KIND_WORDS[i.kind]}: {i.watch}</span>
            {i.status === "added" && <Badge tone="success">lead made</Badge>}</div>
          {i.status === "new" && canWrite() && (
            <div className="dp-article-actions">
              <Input aria-label="Company name" placeholder="Company this is about" value={nameFor(i)} maxLength={80}
                onChange={(e) => setNames({ ...names, [i.id]: e.target.value })} />
              <Button disabled={busy || nameFor(i).trim().length < 2}
                onClick={() => act(() => api.signalToLead(i.id, nameFor(i).trim()), (r) => (r.new ? "Lead added." : "That company was already a lead; the article was added to its history."))}>Add as lead</Button>
              <Button variant="secondary" disabled={busy} onClick={() => act(() => api.signalStatus(i.id, "dismissed"), "Dismissed.")}>Dismiss</Button>
            </div>
          )}
        </Card>
      ))}
      </div>
    </>
  );
}
