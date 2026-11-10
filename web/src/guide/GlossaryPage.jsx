// Help centre, Glossary tab: every term A to Z, searchable, with deep links (#help/glossary/MQL).
import { useEffect, useMemo, useRef, useState } from "react";
import { GLOSSARY } from "../glossary.js";

export default function GlossaryPage({ focusId }) {
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const ws = q.toLowerCase().split(/\s+/).filter(Boolean);
    return GLOSSARY.filter((g) => ws.every((w) => `${g.term} ${g.full} ${g.short} ${g.long} ${g.where}`.toLowerCase().includes(w)));
  }, [q]);
  const letters = [...new Set(shown.map((g) => g.term[0].toUpperCase()))];
  const done = useRef("");
  useEffect(() => {
    if (!focusId || done.current === focusId) return;
    const el = document.getElementById(`gloss-${focusId}`);
    if (el) { done.current = focusId; el.scrollIntoView({ block: "center" }); el.focus({ preventScroll: true }); }
  }, [focusId, shown]);
  return (
    <section aria-labelledby="gloss-title">
      <h3 id="gloss-title">Glossary</h3>
      <p className="sub">Short words used in the portal, in plain English. {GLOSSARY.length} terms. In the portal, dotted underlined words open a small box with the same meaning.</p>
      <form role="search" className="help-search" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="gloss-q" className="sr-only">Search the glossary</label>
        <input id="gloss-q" type="search" placeholder="Search: MQL, bounce, consent, workspace…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="glossary-search" />
      </form>
      <p className="g-az" aria-label="Jump to a letter">{letters.map((l) => <a key={l} href={`#gloss-letter-${l}`} onClick={(e) => { e.preventDefault(); document.getElementById(`gloss-letter-${l}`)?.scrollIntoView(); }}>{l}</a>)}</p>
      <p className="sub" role="status" aria-live="polite">{shown.length} of {GLOSSARY.length} terms</p>
      {shown.length === 0 && <p className="empty">No term matches “{q}”. Try one word.</p>}
      <dl className="g-glossary">
        {shown.map((g, i) => {
          const first = i === 0 || shown[i - 1].term[0].toUpperCase() !== g.term[0].toUpperCase();
          return (
            <div key={g.id} id={`gloss-${g.id}`} tabIndex={-1} className={`g-term${focusId === g.id ? " is-focus" : ""}`} data-testid={`gloss-${g.id}`}>
              {first && <span id={`gloss-letter-${g.term[0].toUpperCase()}`} className="g-letter">{g.term[0].toUpperCase()}</span>}
              <dt><b>{g.term}</b>{g.full && g.full !== g.term && <span className="g-full"> — {g.full}</span>} <small className="pill">{g.group}</small></dt>
              <dd><p className="g-short">{g.short}</p><p>{g.long}</p><p className="sub"><b>Where you see it:</b> {g.where}</p>
                <a className="g-permalink" href={`#help/glossary/${g.id}`}>Link to this term</a></dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
