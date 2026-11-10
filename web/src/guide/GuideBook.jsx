// Help centre, User guide tab: table of contents, search, one chapter at a time, role-aware, print friendly.
import { useEffect, useMemo, useRef, useState } from "react";
import { GROUPS, GUIDE_UPDATED, GUIDE_VERSION } from "./meta.js";
import { badgesFor, chapterById, visibleChapters } from "./chapters.js";
import { Blocks, Inline } from "./markup.jsx";
import { searchGuide } from "./search.js";

function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 860px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

function Badges({ chapter }) {
  return <span className="g-badges">{badgesFor(chapter).map((b) => <span key={b} className="pill g-badge">{b}</span>)}</span>;
}

function Toc({ chapters, current, onPick }) {
  return (
    <nav className="g-toc" aria-label="Guide contents">
      {GROUPS.map((g) => {
        const list = chapters.filter((c) => c.group === g);
        if (!list.length) return null;
        return (
          <div key={g} role="group" aria-label={g}>
            <span className="g-toc-title">{g}</span>
            <ul>{list.map((c) => (
              <li key={c.id}><a href={`#help/${c.id}`} aria-current={c.id === current ? "page" : undefined} className={c.id === current ? "on" : ""}
                data-testid={`toc-${c.id}`} onClick={onPick}>{c.title}</a></li>
            ))}</ul>
          </div>
        );
      })}
    </nav>
  );
}

function Overview({ chapters }) {
  return (
    <>
      <h3 className="g-h" tabIndex={-1} id="guide-top">How to use the portal</h3>
      <p><Inline text="This guide shows each person only the parts their login can use. Pick a chapter on the left, or search. Short words such as [[MQL]] have a dotted line under them: hover, tap or tab to them to see what they mean, or open the [Glossary](#help/glossary)." /></p>
      <p className="sub">Guide version {GUIDE_VERSION} · last updated {GUIDE_UPDATED}</p>
      {GROUPS.map((g) => {
        const list = chapters.filter((c) => c.group === g);
        if (!list.length) return null;
        return (
          <section key={g} aria-label={g}>
            <h4>{g}</h4>
            <div className="help-grid">{list.map((c) => (
              <a key={c.id} className="card help-card g-card" href={`#help/${c.id}`}><h5>{c.title}</h5><p className="sub">{c.summary}</p><Badges chapter={c} /></a>
            ))}</div>
          </section>
        );
      })}
    </>
  );
}

export default function GuideBook({ chapterId, topic, ctx }) {
  const chapters = useMemo(() => visibleChapters(ctx), [ctx.role, ctx.client]);
  const [query, setQuery] = useState("");
  const [printAll, setPrintAll] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const narrow = useNarrow();
  const chapter = chapterId ? chapterById(chapterId) : null;
  const hidden = chapter && !chapters.includes(chapter);
  const hits = useMemo(() => searchGuide(query, chapters), [query, chapters]);
  const main = useRef(null);
  const idx = chapter ? chapters.indexOf(chapter) : -1;

  useEffect(() => {
    if (!chapterId) return;
    const el = topic && document.getElementById(`${chapterId}--${topic}`);
    if (el) { el.scrollIntoView({ block: "start" }); el.focus({ preventScroll: true }); } else { window.scrollTo(0, 0); main.current?.focus({ preventScroll: true }); }
  }, [chapterId, topic, query]);
  useEffect(() => {
    const off = () => setPrintAll(false);
    window.addEventListener("afterprint", off);
    return () => window.removeEventListener("afterprint", off);
  }, []);
  const print = () => { setPrintAll(true); setTimeout(() => window.print(), 50); };
  const closeToc = () => { setTocOpen(false); setQuery(""); };

  return (
    <div className="g-layout">
      <a className="g-skip" href="#guide-main" onClick={(e) => { e.preventDefault(); main.current?.focus(); }}>Skip to the guide text</a>
      <aside className="g-side">
        <form role="search" className="help-search" onSubmit={(e) => e.preventDefault()}>
          <label htmlFor="guide-q" className="sr-only">Search the guide and glossary</label>
          <input id="guide-q" type="search" placeholder="Search the guide: import, MQL, bounce…" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="guide-search" />
        </form>
        <details className="g-toc-wrap" open={!narrow || tocOpen}>
          <summary onClick={(e) => { e.preventDefault(); setTocOpen(!tocOpen); }}>Contents</summary>
          <Toc chapters={chapters} current={chapterId} onPick={closeToc} />
        </details>
      </aside>
      <article id="guide-main" className={`g-main${printAll ? " print-all" : ""}`} ref={main} tabIndex={-1} aria-label="Guide text">
        {query.trim() ? (
          <section aria-label="Search results" data-testid="guide-results">
            <h3>{hits.length} result{hits.length === 1 ? "" : "s"} for “{query}”</h3>
            {hits.length === 0 && <p className="empty">Nothing found. Try one word, like “import” or “consent”.</p>}
            <ul className="g-hits">{hits.map((h) => (
              <li key={`${h.type}-${h.id}`}><a href={h.href} onClick={() => setQuery("")}><b>{h.title}</b></a> <span className="pill">{h.type === "term" ? "Glossary" : "Guide"}</span><br /><span className="sub">{h.text}</span></li>
            ))}</ul>
          </section>
        ) : !chapterId ? <Overview chapters={chapters} />
          : !chapter ? <><h3 tabIndex={-1}>Chapter not found</h3><p>There is no chapter called “{chapterId}”. Pick one from the contents.</p></>
          : hidden ? <><h3 tabIndex={-1}>{chapter.title}</h3><p className="empty" role="status">This chapter is for logins with other permissions, so it is not shown for yours.</p></>
          : (
            <>
              <div className="g-bar no-print">
                <label className="row" style={{ gap: 6, margin: 0 }}><input type="checkbox" checked={printAll} onChange={(e) => setPrintAll(e.target.checked)} /> Include every chapter</label>
                <button type="button" className="btn ghost" onClick={print} data-testid="guide-print">Print / save as PDF</button>
              </div>
              {(printAll ? chapters : [chapter]).map((c) => (
                <section key={c.id} className="g-chapter" aria-labelledby={`${c.id}-title`} data-testid={`chapter-${c.id}`}>
                  <h3 id={`${c.id}-title`} className="g-title">{c.title} <Badges chapter={c} /></h3>
                  <p className="g-lead"><Inline text={c.summary} /></p>
                  <Blocks chapter={c} />
                </section>
              ))}
              <nav className="g-pager no-print" aria-label="Previous and next chapter">
                {idx > 0 ? <a href={`#help/${chapters[idx - 1].id}`}>← {chapters[idx - 1].title}</a> : <span />}
                {idx < chapters.length - 1 ? <a href={`#help/${chapters[idx + 1].id}`}>{chapters[idx + 1].title} →</a> : <span />}
              </nav>
              <p className="sub g-foot">Guide version {GUIDE_VERSION} · last updated {GUIDE_UPDATED}</p>
            </>
          )}
      </article>
    </div>
  );
}
