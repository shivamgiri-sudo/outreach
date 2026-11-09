// Public legal pages (Terms, Privacy, DPA). Static content, no login; only the version labels come from /api/signup/config.
import { useEffect, useState } from "react";
import { DOCS, LEGAL_PATHS, placeholdersOf } from "../legal/index.js";
import "../legal.css";

const NAMES = { terms: "Terms of Service", privacy: "Privacy Policy", dpa: "Data Processing Agreement" };

function Block({ b }) {
  if (typeof b === "string") return <p>{b}</p>;
  if (Array.isArray(b)) return <ul>{b.map((x, i) => <li key={i}>{x}</li>)}</ul>;
  return (
    <div className="legal-table-wrap" tabIndex={0} role="region" aria-label="Table, scrolls sideways on small screens">
      <table>
        <thead><tr>{b.head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function jump(e, id) {
  const el = document.getElementById(id);
  if (!el) return;
  e.preventDefault();                       // a plain #hash would be read as a page route by the app
  el.scrollIntoView({ block: "start", behavior: "instant" });
  el.focus({ preventScroll: true });
}

export default function Legal({ doc: key }) {
  const doc = DOCS[key];
  const [versions, setVersions] = useState(null);
  const [failed, setFailed] = useState(false);
  const placeholders = placeholdersOf(doc);
  useEffect(() => {
    document.title = `${doc.title} - MCN Outreach`;
    let off = false;
    fetch("/api/signup/config").then((r) => (r.ok ? r.json() : null)).then((c) => { if (off) return; if (c?.terms) setVersions(c.terms); else setFailed(true); }).catch(() => { if (!off) setFailed(true); });
    return () => { off = true; };
  }, [doc]);
  const version = versions?.[key];
  return (
    <div className="legal" data-testid={`legal-${key}`}>
      <a className="legal-skip" href="#legal-main" onClick={(e) => jump(e, "legal-main")}>Skip to the document</a>
      <header className="legal-top">
        <a className="legal-brand" href="/">MCN Outreach</a>
        <nav aria-label="Legal documents">
          {LEGAL_PATHS.map((p) => <a key={p} href={`/${p}`} aria-current={p === key ? "page" : undefined}>{NAMES[p]}</a>)}
        </nav>
      </header>
      <main id="legal-main" tabIndex={-1}>
        <div className="legal-draft" role="note" data-testid="legal-draft">
          <strong>DRAFT - for legal review</strong>
          <p>This document has not been reviewed by a lawyer and is not yet in force. Fill every placeholder below before it is published.</p>
          <p className="legal-meta" data-testid="legal-version">
            Version {version || (failed ? "(unavailable, reload)" : "...")} &middot; effective [EFFECTIVE DATE - set on approval]
          </p>
          <details open>
            <summary>{placeholders.length + 1} placeholders to fill</summary>
            <ul className="legal-ph">{[...placeholders, "[EFFECTIVE DATE - set on approval]"].map((p) => <li key={p}><code>{p}</code></li>)}</ul>
          </details>
        </div>
        <h1>{doc.title}</h1>
        <p className="legal-lede">{doc.lede}</p>
        <nav className="legal-toc" aria-label="Contents">
          <h2>Contents</h2>
          <ol>{doc.sections.map((s) => <li key={s.id}><a href={`#${key}-${s.id}`} onClick={(e) => jump(e, `${key}-${s.id}`)}>{s.title.replace(/^\d+\.\s*/, "")}</a></li>)}</ol>
        </nav>
        {doc.sections.map((s) => (
          <section key={s.id} aria-labelledby={`${key}-${s.id}-h`}>
            <h2 id={`${key}-${s.id}`} tabIndex={-1}><span id={`${key}-${s.id}-h`}>{s.title}</span></h2>
            {s.blocks.map((b, i) => <Block key={i} b={b} />)}
          </section>
        ))}
      </main>
      <footer className="legal-foot">
        <span>Mas Callnet India Pvt Ltd</span>
        <a href="/">Back to MCN Outreach</a>
      </footer>
    </div>
  );
}
