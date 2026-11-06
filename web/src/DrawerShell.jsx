// Shared shell for side panels: scrim, Escape to close, focus kept inside, focus returned on close.
// useModalFocus is also used by the phone menu and by Dialog.jsx. Only the top-most open panel answers
// Escape and Tab, so a confirm box over a drawer closes just the confirm box.
import { useEffect, useRef } from "react";

const stack = [];
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(el) {
  return [...el.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null || n === document.activeElement);
}

export function useModalFocus(panel, onClose, active = true, initial = null) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!active) return undefined;
    const token = {};
    stack.push(token);
    const previous = document.activeElement;
    const el = panel.current;
    const first = (initial && el?.querySelector(initial)) || el?.querySelector("h2[tabindex], [data-autofocus]") || (el && focusables(el)[0]);
    first?.focus?.();
    const onKey = (e) => {
      if (stack[stack.length - 1] !== token || !panel.current) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close.current?.(); return; }
      if (e.key !== "Tab") return;
      const f = focusables(panel.current);
      if (!f.length) { e.preventDefault(); return; }
      const [a, z] = [f[0], f[f.length - 1]];
      if (!panel.current.contains(document.activeElement)) { e.preventDefault(); a.focus(); return; }
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      const i = stack.indexOf(token);
      if (i >= 0) stack.splice(i, 1);
      if (previous && document.contains(previous)) previous.focus?.();
    };
  }, [active]);
}

export default function DrawerShell({ onClose, labelledBy, label, className = "", testId, style, children }) {
  const panel = useRef(null);
  useModalFocus(panel, onClose);
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className={`drawer ${className}`.trim()} role="dialog" aria-modal="true" aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : label} data-testid={testId} style={style}>
        {children}
      </div>
    </>
  );
}
