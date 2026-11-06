// "How do I get this?": a button that opens a plain-English setup guide in a side panel, plus field-level help.
import { useEffect, useRef, useState } from "react";
import { useModalFocus } from "./DrawerShell.jsx";
import { checkValue, guideIds, useGuides } from "./guides.js";
import "./guides.css";

export function GuideView({ guide, onOpen, headingLevel = 3 }) {
  const H = `h${headingLevel}`;
  return (
    <div className="guide-view" data-testid={`guide-${guide.id}`}>
      <p className="guide-summary">{guide.summary}</p>
      <ul className="guide-facts" aria-label="At a glance">
        {guide.time && <li><b>Time:</b> {guide.time}</li>}
        {guide.cost && <li><b>Cost:</b> {guide.cost}</li>}
      </ul>
      <H>Steps</H>
      <ol className="steps guide-steps">{guide.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      {guide.paste.length > 0 && (
        <>
          <H>What to paste where</H>
          <dl className="guide-paste">
            {guide.paste.map(([label, what]) => <div key={label}><dt>{label}</dt><dd>{what}</dd></div>)}
          </dl>
        </>
      )}
      {guide.test && <><H>How to check it works</H><p>{guide.test}</p></>}
      {guide.errors.length > 0 && (
        <>
          <H>Common problems</H>
          <div className="guide-errors">
            {guide.errors.map(([problem, fix]) => (
              <details key={problem}><summary>{problem}</summary><p>{fix}</p></details>
            ))}
          </div>
        </>
      )}
      {guide.safety && <div className="tip guide-safety" role="note"><b>Stay safe: </b>{guide.safety}</div>}
      {guide.links.length > 0 && (
        <p className="guide-links">
          {guide.links.map(([label, url]) => (
            <a key={url} href={url} target="_blank" rel="noreferrer">{label} ↗<span className="sr-only"> (opens in a new tab)</span></a>
          ))}
        </p>
      )}
      {guide.related.length > 0 && onOpen && (
        <p className="guide-related"><b>See also: </b>
          {guide.related.map((id) => <RelatedLink key={id} id={id} onOpen={onOpen} />)}
        </p>
      )}
    </div>
  );
}

function RelatedLink({ id, onOpen }) {
  const { data } = useGuides();
  const g = data?.byId[id];
  return g ? <button type="button" className="linkish" onClick={() => onOpen(id)}>{g.title}</button> : null;
}

export function GuidePanel({ ids, startId, onClose }) {
  const { data, error } = useGuides();
  const [current, setCurrent] = useState(startId || ids[0]);
  const panel = useRef(null);
  useModalFocus(panel, onClose);
  useEffect(() => { panel.current?.querySelector(".guide-body")?.scrollTo?.(0, 0); }, [current]);
  const guide = data?.byId[current];
  const others = ids.filter((id) => id !== current && data?.byId[id]);
  return (
    <>
      <div className="scrim guide-scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer guide-drawer" role="dialog" aria-modal="true" aria-labelledby="guide-title" ref={panel} data-testid="guide-panel">
        <div className="drawer-head">
          <div><span className="sub-inline">How do I get this?</span>
            <h2 id="guide-title" tabIndex={-1}>{guide ? guide.title : "Setup guide"}</h2></div>
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </div>
        <div className="guide-body">
          {error && <div className="tip error" role="alert">The guide could not load: {error}</div>}
          {!guide && !error && <p className="sub">Loading…</p>}
          {guide && <GuideView guide={guide} onOpen={setCurrent} />}
          {others.length > 0 && (
            <div className="guide-others"><b>Other guides for this</b>
              <ul>{others.map((id) => <li key={id}><button type="button" className="linkish" onClick={() => setCurrent(id)}>{data.byId[id].title}</button></li>)}</ul>
            </div>
          )}
          <p className="field-help"><a href="#help">Browse every guide in the Help centre</a></p>
        </div>
      </div>
    </>
  );
}

// keys: one screen key or a list ("hub:email_send:resend", "integration:apollo", "setup:public_url" ...).
export function GuideButton({ keys, label = "How do I get this?", className = "linkish guide-link", testId }) {
  const { data } = useGuides();
  const [open, setOpen] = useState(false);
  const ids = guideIds(data, keys);
  if (data && !ids.length) return null;
  return (
    <>
      <button type="button" className={className} aria-haspopup="dialog" onClick={() => setOpen(true)} data-testid={testId || "guide-button"}>
        <span aria-hidden="true">?</span> {label}
      </button>
      {open && <GuidePanel ids={ids} onClose={() => setOpen(false)} />}
    </>
  );
}

// Field-level help under an input: what the value looks like, and a friendly warning once the box is left.
export function FieldHint({ id, help, value, touched }) {
  if (!help) return null;
  const problem = touched ? checkValue(help, value) : "";
  return (
    <>
      <p className="field-help guide-hint" id={`${id}-hint`}>Looks like: {help.hint}</p>
      <p className={`guide-problem ${problem ? "on" : ""}`} id={`${id}-problem`} aria-live="polite">{problem}</p>
    </>
  );
}

export function useTouched() {
  const [touched, setTouched] = useState({});
  return [touched, (key) => setTouched((t) => (t[key] ? t : { ...t, [key]: true }))];
}
