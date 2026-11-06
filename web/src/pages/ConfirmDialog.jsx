import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

// In-app confirmation (instead of window.confirm): a centred card over a scrim. Escape or Cancel closes it.
// Rendered into <body> so a sticky parent (the bulk bar) can't trap it under the page.
export default function ConfirmDialog({ title, children, confirmLabel = "Yes, go ahead", danger = false, busy = false, onConfirm, onCancel, testId = "confirm-dialog" }) {
  const yes = useRef(null);
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  useEffect(() => {
    yes.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") cancel.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return createPortal(
    <>
      <div className="scrim" onClick={onCancel} />
      <div className="palette" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" data-testid={testId}
        style={{ padding: "18px 20px", left: 16, right: 16, width: "auto", maxWidth: 560, margin: "0 auto", transform: "none", animation: "fade .2s ease both" }}>
        <h3 id="confirm-title" style={{ marginTop: 0 }}>{title}</h3>
        <div className="sub" style={{ color: "inherit" }}>{children}</div>
        <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
          <button className="btn ghost" onClick={onCancel}>Cancel</button>
          <button ref={yes} className={danger ? "btn danger-solid" : "btn"} disabled={busy} onClick={onConfirm}>{busy ? "Working…" : confirmLabel}</button>
        </div>
      </div>
    </>,
    document.body,
  );
}
