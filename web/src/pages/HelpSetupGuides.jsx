// Setup guides tab of the Help centre: every "How do I get this?" guide, searchable, grouped by topic. #help?guide=<id> opens one directly.
import { useMemo, useState } from "react";
import { GuidePanel } from "../GuidePanel.jsx";
import { searchGuides, useGuides } from "../guides.js";
import { ErrorState } from "../states.jsx";

const ORDER = ["Email sending", "Reply capture", "Bounces", "WhatsApp", "SMS", "Calls", "Calendar & meetings", "Lead capture",
  "Lead data", "Email verification", "AI", "App & team", "Custom"];

function startGuide() {
  return new URLSearchParams(window.location.hash.split("?")[1] || "").get("guide") || "";
}

export default function HelpSetupGuides() {
  const { data, error } = useGuides();
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState("");
  const [open, setOpen] = useState(startGuide);
  const shown = useMemo(() => (data ? searchGuides(data.guides, query).filter((g) => !cat || g.category === cat) : []), [data, query, cat]);
  if (error) return <ErrorState message="The Help centre could not load." detail={error} onRetry={() => window.location.reload()} />;
  if (!data) return <p className="empty">Loading…</p>;
  const cats = ORDER.filter((c) => data.guides.some((g) => g.category === c));
  const groups = cats.map((c) => [c, shown.filter((g) => g.category === c)]).filter(([, list]) => list.length);
  const close = () => { setOpen(""); if (window.location.hash.includes("guide=")) window.history.replaceState(null, "", "#help"); };

  return (
    <>
      <h3>Setup guides</h3>
      <p className="sub">Step-by-step guides for every key, password and connection the app asks for. Written for people with no technical background.</p>
      <form className="help-search" role="search" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="help-q" className="sr-only">Search the guides</label>
        <input id="help-q" type="search" placeholder="Search: gmail, whatsapp, DLT, api key, domain…" value={query}
          onChange={(e) => setQuery(e.target.value)} data-testid="help-search" />
      </form>
      <div className="help-cats" role="group" aria-label="Filter by topic">
        <button type="button" className="btn ghost" aria-pressed={!cat} onClick={() => setCat("")}>All</button>
        {cats.map((c) => <button type="button" key={c} className="btn ghost" aria-pressed={cat === c} onClick={() => setCat(cat === c ? "" : c)}>{c}</button>)}
      </div>
      <p className="sub" role="status" aria-live="polite">{shown.length} of {data.guides.length} guides</p>
      {groups.length === 0 && <p className="empty">No guide matches “{query}”. Try a shorter word, like “email” or “key”.</p>}
      {groups.map(([c, list]) => (
        <section key={c} aria-labelledby={`help-${c}`}>
          <h4 id={`help-${c}`}>{c}</h4>
          <div className="help-grid">
            {list.map((g) => (
              <article key={g.id} className="card help-card" data-testid={`help-card-${g.id}`}>
                <h5>{g.title}</h5>
                <p className="sub">{g.summary}</p>
                {g.time && <small className="sub-inline">Time: {g.time}</small>}
                <button type="button" className="btn ghost" onClick={() => setOpen(g.id)} aria-label={`Open guide: ${g.title}`}>Open guide</button>
              </article>
            ))}
          </div>
        </section>
      ))}
      {open && data.byId[open] && <GuidePanel ids={[open]} startId={open} onClose={close} />}
    </>
  );
}
