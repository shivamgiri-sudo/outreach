import { useEffect, useState } from "react";
import { istDateLabel, istLabel } from "../time.js";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { PageHeader } from "../ui/index.js";

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
      <section className="card" style={{ marginBottom: 14 }}>
        {isOwner() && <div className="row">
          <select aria-label="What to watch" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND_WORDS).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
          </select>
          <input aria-label="Words to watch" value={text} maxLength={80} onChange={(e) => setText(e.target.value)}
            placeholder={kind === "keyword" ? "BPO hiring Noida" : kind === "competitor" ? "A competitor's name" : "A company you want to track"}
            onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) act(async () => { await api.addWatch(kind, text.trim()); setText(""); }, "Added."); }} />
          <button className="btn" disabled={busy || text.trim().length < 2} onClick={() => act(async () => { await api.addWatch(kind, text.trim()); setText(""); }, "Added.")}>Watch</button>
          <button className="btn ghost" disabled={busy || !(data?.watch || []).length}
            onClick={() => act(() => api.runSignals(), (r) => `${r.new} new article(s)${r.errors?.length ? `, ${r.errors.length} feed(s) failed` : ""}.`)}>Check now</button>
        </div>}
        <div className="chips" style={{ marginTop: 8 }}>
          {(data?.watch || []).map((w) => (
            <span key={w.id} className="chip">{KIND_WORDS[w.kind]}: {w.text}{" "}
              {isOwner() && <button type="button" className="link" aria-label={`Stop watching ${w.text}`} onClick={() => act(() => api.dropWatch(w.id), "Removed.")}>×</button>}</span>
          ))}
          {data && data.watch.length === 0 && <span className="sub">Nothing watched yet.</span>}
        </div>
        {data?.last_run && <p className="sub" style={{ marginBottom: 0 }}>Last checked {istLabel(data.last_run)}.</p>}
        {msg && <p role="status" className="sub">{msg}</p>}
      </section>
      <div className="row" style={{ marginBottom: 10 }}>
        <select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value)}>
          <option value="new">New</option><option value="added">Made into leads</option><option value="dismissed">Dismissed</option><option value="all">All</option>
        </select>
      </div>
      {data && items.length === 0 && <p className="empty">No articles here.{isOwner() ? " Add something to watch and press Check now." : ""}</p>}
      {items.map((i) => (
        <div key={i.id} className="card" style={{ marginBottom: 10 }}>
          <div><a href={i.link} target="_blank" rel="noopener noreferrer">{i.title}</a></div>
          <div className="sub">{i.source}{i.published ? ` · ${istDateLabel(i.published)}` : ""} · {KIND_WORDS[i.kind]}: {i.watch}
            {i.status === "added" && <> · <span className="pill ok">lead made</span></>}</div>
          {i.status === "new" && canWrite() && (
            <div className="row" style={{ marginTop: 6 }}>
              <input aria-label="Company name" placeholder="Company this is about" value={nameFor(i)} maxLength={80}
                onChange={(e) => setNames({ ...names, [i.id]: e.target.value })} />
              <button className="btn" disabled={busy || nameFor(i).trim().length < 2}
                onClick={() => act(() => api.signalToLead(i.id, nameFor(i).trim()), (r) => (r.new ? "Lead added." : "That company was already a lead; the article was added to its history."))}>Add as lead</button>
              <button className="btn ghost" disabled={busy} onClick={() => act(() => api.signalStatus(i.id, "dismissed"), "Dismissed.")}>Dismiss</button>
            </div>
          )}
        </div>
      ))}
    </>
  );
}
