import { useEffect, useMemo, useRef, useState } from "react";
import { GROUPS, TYPES, iconPath } from "./catalog.js";

// An n8n-style "what happens next?" box: type to search, arrow keys to move, Enter to add, Escape to close.
export default function NodePicker({ open, onClose, onPick, heading = "Add a step", onlyTriggers = false }) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef(null);
  const list = useRef(null);

  useEffect(() => { if (open) { setQuery(""); setCursor(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    return Object.entries(TYPES)
      .filter(([type, t]) => !(onlyTriggers && !type.startsWith("trigger.")))
      .filter(([type, t]) => !q || `${t[1]} ${t[5]} ${type} ${t[0]}`.toLowerCase().includes(q))
      .map(([type, t]) => ({ type, group: t[0], label: t[1], icon: t[2], help: t[5] }))
      .sort((a, b) => GROUPS.findIndex(([g]) => g === a.group) - GROUPS.findIndex(([g]) => g === b.group));
  }, [query, onlyTriggers]);

  useEffect(() => { if (cursor >= items.length) setCursor(Math.max(0, items.length - 1)); }, [items.length, cursor]);
  useEffect(() => { list.current?.querySelector(".on")?.scrollIntoView({ block: "nearest" }); }, [cursor]);

  if (!open) return null;
  const choose = (item) => { if (item) { onPick(item.type); onClose(); } };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(items.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[cursor]); }
  };
  let index = -1;
  return (
    <div className="fb-picker-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="fb-picker" role="dialog" aria-modal="true" aria-label={heading} onKeyDown={onKey}>
        <div className="fb-picker-head">
          <b>{heading}</b>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">Esc</button>
        </div>
        <input ref={input} className="fb-picker-search" type="search" placeholder="Search steps: email, wait, Claude, switch…" value={query}
          onChange={(e) => { setQuery(e.target.value); setCursor(0); }} aria-label="Search steps" />
        <div className="fb-picker-list" ref={list} role="listbox" aria-label="Steps">
          {items.length === 0 && <p className="empty" style={{ padding: 16 }}>No step matches “{query}”.</p>}
          {GROUPS.map(([group, heading2]) => {
            const mine = items.filter((i) => i.group === group);
            if (!mine.length) return null;
            return (
              <div key={group} className="fb-picker-group">
                <span className="nav-title">{heading2}</span>
                {mine.map((item) => {
                  index += 1;
                  const mineIndex = index;
                  return (
                    <button key={item.type} type="button" role="option" aria-selected={cursor === mineIndex} aria-label={`Add step: ${item.label}`}
                      className={`fb-picker-item g-${group}${cursor === mineIndex ? " on" : ""}`} onMouseEnter={() => setCursor(mineIndex)} onClick={() => choose(item)}>
                      <span className="fb-picker-icon"><svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={iconPath(item.icon)} /></svg></span>
                      <span className="fb-picker-text"><b>{item.label}</b><small>{item.help}</small></span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
