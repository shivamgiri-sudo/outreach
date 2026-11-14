import { useId } from "react";
import Icon from "../icons.jsx";

const cx = (...a) => a.filter(Boolean).join(" ");

// Label + control + help text, wired together for screen readers. children = the <Input>/<Select>.
export function Field({ label, help, children, className }) {
  return (
    <label className={cx("field", className)}>
      {label && <span className="field-label">{label}</span>}
      {children}
      {help && <span className="field-help">{help}</span>}
    </label>
  );
}

// Text input, 40px high, optional leading icon and a clear button when it has text.
export function Input({ icon, onClear, className, value, ...rest }) {
  if (!icon && !onClear) return <input className={className} value={value} {...rest} />;
  return (
    <span className="field-wrap">
      {icon && <Icon name={icon} size={16} />}
      <input className={className} value={value} {...rest} />
      {onClear && value ? <button type="button" className="icon-btn sm field-clear" aria-label="Clear" onClick={onClear}><Icon name="close" size={14} /></button> : null}
    </span>
  );
}

export function Select({ icon, className, children, ...rest }) {
  if (!icon) return <select className={className} {...rest}>{children}</select>;
  return <span className="field-wrap"><Icon name={icon} size={16} /><select className={className} {...rest}>{children}</select></span>;
}

// An accessible on/off switch (a real checkbox with role=switch). label is the accessible name; showState prints On/Off.
export function Switch({ checked, onChange, label, showState, disabled, ...rest }) {
  return (
    <label className="ui-switch">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.checked)} {...rest} />
      <span className="track" aria-hidden="true" />
      {showState && <span className="state" aria-hidden="true">{checked ? "On" : "Off"}</span>}
    </label>
  );
}

// Underlined tabs: items = [{ id, label, count?, icon? }].
export function Tabs({ items, value, onChange, label, className }) {
  return (
    <div className={cx("tabs", className)} role="tablist" aria-label={label}>
      {items.map((t) => (
        <button key={t.id} type="button" role="tab" data-testid={t.testId} aria-selected={value === t.id} className={value === t.id ? "on" : ""} onClick={() => onChange(t.id)}>
          {t.icon && <Icon name={t.icon} size={16} />}{t.label}{t.count != null && <span className="pill">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// Segmented control for views and modes (one selected, always visibly styled).
export function Segmented({ items, value, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {items.map((t) => (
        <button key={t.id} type="button" aria-pressed={value === t.id} className={value === t.id ? "on" : ""} onClick={() => onChange(t.id)}>
          {t.label}{t.count != null && <small className="sub-inline">{t.count}</small>}
        </button>
      ))}
    </div>
  );
}

// Removable filter chip.
export function Chip({ children, onRemove, label }) {
  const id = useId();
  return (
    <span className="ui-chip" id={id}>{children}
      {onRemove && <button type="button" aria-label={label || "Remove filter"} onClick={onRemove}><Icon name="close" size={12} /></button>}
    </span>
  );
}
