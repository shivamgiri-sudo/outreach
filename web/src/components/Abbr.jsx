// <Abbr term="MQL">MQL</Abbr>: an abbreviation with its full name in a tooltip.
//   hover and keyboard focus open it, a tap or click pins it, Esc or a tap elsewhere closes it.
//   Screen readers always get "Full name. Meaning" through aria-describedby (the visible bubble is a copy and is hidden from them).
//   The bubble is position: fixed and clamped to the screen, so it is never cut off on a phone or inside a scrolling table.
// Unknown terms render the plain children and warn in development, so a typo never breaks a page.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { findTerm, glossaryHref } from "../glossary.js";
import "./abbr.css";

const WIDTH = 300;
const MARGIN = 10;

function place(anchor) {
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const w = Math.min(WIDTH, vw - MARGIN * 2);
  const left = Math.max(MARGIN, Math.min(r.left, vw - w - MARGIN));
  const below = vh - r.bottom > 170 || r.top < 170;
  return below ? { left, width: w, top: r.bottom + 6 } : { left, width: w, bottom: vh - r.top + 6 };
}

export default function Abbr({ term, children }) {
  const entry = findTerm(term);
  const id = useId();
  const wrap = useRef(null);
  const anchor = useRef(null);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState(null);
  const timer = useRef(0);
  const open = hover || focus || pinned;

  useLayoutEffect(() => { if (open && anchor.current) setPos(place(anchor.current)); }, [open]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { setHover(false); setFocus(false); setPinned(false); } };
    const onDown = (e) => { if (wrap.current && !wrap.current.contains(e.target)) { setPinned(false); setHover(false); } };
    const reposition = () => anchor.current && setPos(place(anchor.current));
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open]);
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!entry) {
    if (import.meta.env?.DEV) console.warn(`<Abbr>: "${term}" is not in the glossary`);
    return <>{children ?? term}</>;
  }
  const full = entry.full || entry.term;
  const leave = () => { clearTimeout(timer.current); timer.current = setTimeout(() => setHover(false), 120); };
  const enter = () => { clearTimeout(timer.current); setHover(true); };
  const toggle = (e) => { e.preventDefault(); e.stopPropagation(); setPinned((p) => !p); };
  const onKeyDown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); setPinned((p) => !p); } };
  return (
    <span className="abbr-wrap" ref={wrap} onMouseEnter={enter} onMouseLeave={leave}
      onBlur={(e) => { if (!wrap.current?.contains(e.relatedTarget)) { setFocus(false); setPinned(false); } }}>
      <abbr ref={anchor} className="abbr" tabIndex={0} data-term={entry.id} aria-describedby={`${id}-d`}
        onFocus={() => setFocus(true)} onClick={toggle} onKeyDown={onKeyDown}>{children ?? entry.term}</abbr>
      <span id={`${id}-d`} hidden>{`${full}. ${entry.short}`}</span>
      {open && pos && (
        <span className="abbr-tip" role="presentation" style={pos} data-testid="abbr-tip" onClick={(e) => e.stopPropagation()}>
          <b>{full}</b>
          <span>{entry.short}</span>
          <a href={glossaryHref(entry.term)} onClick={() => { setPinned(false); setHover(false); setFocus(false); }}>Learn more</a>
        </span>
      )}
    </span>
  );
}

// A sales stage name for people: "Marketing Qualified Lead (MQL)" with the short form explained, or only the short form.
const STAGE_TERM = { MQL: "MQL", SAL: "SAL", SQL: "SQL" };
const SHORT = { "Inquiry / Raw Lead": "Inquiry", "Discovery Meeting Held": "Meeting", "Proposal Submitted": "Proposal",
  "Negotiation / Commercials": "Negotiation", "Closed Won": "Won", "Closed Lost": "Lost", "Recycled / Nurture": "Recycled" };
export function Stage({ status, full = false, plain = false }) {
  const m = /^(.*) \((MQL|SAL|SQL)\)$/.exec(status || "");
  const code = m ? m[2] : STAGE_TERM[status];
  if (code) {
    const name = m ? m[1] : "";
    if (plain) return <>{full && name ? `${name} (${code})` : code}</>;
    return full && name ? <>{name} (<Abbr term={code}>{code}</Abbr>)</> : <Abbr term={code}>{code}</Abbr>;
  }
  return <>{full ? status : SHORT[status] || status}</>;
}
