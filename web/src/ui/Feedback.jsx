import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import Icon from "../icons.jsx";

const TONE = { neutral: "", info: "info", success: "ok", warning: "warn", danger: "err", hot: "hot", muted: "muted" };
export function Badge({ tone = "neutral", className = "", children, ...rest }) {
  return <span className={`pill ${TONE[tone] || ""} ${className}`.trim()} {...rest}>{children}</span>;
}
export const Pill = Badge;

export function EmptyState({ icon = "inbox", title, children, action }) {
  return (
    <div className="ui-empty" role="status">
      <span className="stat-icon"><Icon name={icon} /></span>
      {title && <h3>{title}</h3>}
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ width = "100%", height = 16, style }) {
  return <span className="ui-skel" aria-hidden="true" style={{ width, height, ...style }} />;
}

// <ToastProvider> once near the root; useToast()(message, "ok" | "err") anywhere below it.
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const n = useRef(0);
  const push = useCallback((text, tone = "") => {
    const id = ++n.current;
    setItems((l) => [...l, { id, text, tone }]);
    setTimeout(() => setItems((l) => l.filter((x) => x.id !== id)), 6000);
  }, []);
  useEffect(() => () => { n.current = 0; }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="ui-toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`ui-toast ${t.tone}`}>{t.text}
            <button aria-label="Dismiss" onClick={() => setItems((l) => l.filter((x) => x.id !== t.id))}><Icon name="close" size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
