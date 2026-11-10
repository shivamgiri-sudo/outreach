// Renders chapter blocks. Text inside blocks may use:
//   **bold**            for button and field names as the screen shows them
//   [[MQL]]  [[MQL|shown text]]   an abbreviation with its tooltip (the term must exist in the glossary)
//   {{menu:Label}}      a menu page or Setup section by its exact label (a test checks the label is real)
//   [text](#help/id)    a link to another chapter or glossary entry
// Blocks: ["h", "Heading"] ["p", text] ["steps", [text...]] ["ul", [text...]] ["tip", text] ["warn", text]
//         ["table", { head: [..], rows: [[..]] }] ["img", "file-name", "alt text"] (file is /guide/<file>.webp)
import { Fragment, useState } from "react";
import Abbr from "../components/Abbr.jsx";
import { slug } from "./chapters.js";

const TOKEN = /(\*\*[^*]+\*\*|\[\[[^\]]+\]\]|\{\{menu:[^}]+\}\}|\[[^\]]+\]\(#[^)]+\))/g;

export function Inline({ text }) {
  return String(text).split(TOKEN).map((part, i) => {
    if (part.startsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("[[")) { const [term, shown] = part.slice(2, -2).split("|"); return <Abbr key={i} term={term}>{shown || term}</Abbr>; }
    if (part.startsWith("{{menu:")) return <strong key={i} className="ui-label">{part.slice(7, -2)}</strong>;
    if (part.startsWith("[")) { const m = /^\[([^\]]+)\]\((#[^)]+)\)$/.exec(part); return <a key={i} href={m[2]}>{m[1]}</a>; }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

// Heading with a copy-link button. The link is #help/<chapter>/<heading id>.
function Heading({ chapterId, text, level = 3 }) {
  const [copied, setCopied] = useState(false);
  const id = `${chapterId}--${slug(text)}`;
  const href = `#help/${chapterId}/${slug(text)}`;
  const copy = () => {
    const url = `${window.location.origin}${window.location.pathname}${href}`;
    const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1500); };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(url).then(done, done); else done();
  };
  const H = `h${level}`;
  return (
    <H id={id} className="g-h" tabIndex={-1}>
      {text}
      <button type="button" className="g-copy" onClick={copy} aria-label={`Copy a link to: ${text}`}>{copied ? "Copied" : "Link"}</button>
    </H>
  );
}

export function Blocks({ chapter }) {
  return chapter.blocks.map(([kind, a, b], i) => {
    switch (kind) {
      case "h": return <Heading key={i} chapterId={chapter.id} text={a} />;
      case "p": return <p key={i}><Inline text={a} /></p>;
      case "steps": return <ol key={i} className="g-steps">{a.map((s, j) => <li key={j}><Inline text={s} /></li>)}</ol>;
      case "ul": return <ul key={i} className="g-list">{a.map((s, j) => <li key={j}><Inline text={s} /></li>)}</ul>;
      case "tip": return <div key={i} className="tip g-note" role="note"><strong>Tip: </strong><Inline text={a} /></div>;
      case "warn": return <div key={i} className="tip error g-note" role="note"><strong>Careful: </strong><Inline text={a} /></div>;
      case "table": return (
        <div key={i} className="g-table table-card" style={{ overflowX: "auto" }}>
          <table><thead><tr>{a.head.map((h, j) => <th key={j} scope="col">{h}</th>)}</tr></thead>
            <tbody>{a.rows.map((r, j) => <tr key={j}>{r.map((c, k) => (k === 0 ? <th key={k} scope="row"><Inline text={c} /></th> : <td key={k}><Inline text={c} /></td>))}</tr>)}</tbody></table>
        </div>);
      case "img": return (
        <figure key={i} className="g-shot"><img src={`/guide/${a}.webp`} alt={b} loading="lazy" /><figcaption>{b}</figcaption></figure>);
      default: return null;
    }
  });
}

// Plain text of a block list, for search.
const strip = (t) => t.replace(/\*\*/g, "").replace(/\[\[([^\]|]+)(\|[^\]]*)?\]\]/g, "$1").replace(/\{\{menu:([^}]+)\}\}/g, "$1").replace(/\[([^\]]+)\]\(#[^)]*\)/g, "$1");
export function plainText(blocks) {
  return strip(blocks.map(([kind, a]) => (kind === "steps" || kind === "ul" ? a.join(" ") : kind === "table" ? [...a.head, ...a.rows.flat()].join(" ") : kind === "img" ? "" : a)).join(" "));
}
