import { useEffect, useRef } from "react";

// A click-open panel built on <details>: closes on Escape and when you click outside. label = summary content.
export function Popover({ label, className = "", align, children, summaryClass = "btn ghost", ariaLabel, testId }) {
  const ref = useRef(null);
  useEffect(() => {
    const close = (e) => { if (ref.current?.open && (e.type === "keydown" ? e.key === "Escape" : !ref.current.contains(e.target))) ref.current.open = false; };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, []);
  return (
    <details ref={ref} className={`ui-pop ${align === "right" ? "right" : ""} ${className}`} data-testid={testId}>
      <summary className={summaryClass} aria-label={ariaLabel}>{label}</summary>
      <div className="ui-pop-panel">{children}</div>
    </details>
  );
}
