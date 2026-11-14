import Icon from "../icons.jsx";
import HelpLink from "../components/HelpLink.jsx";

// The one page header: title, one-line description, actions on the right, optional help link. Always an <h2> (tests and
// screen readers look for the page heading there).
export function PageHeader({ title, description, actions, help, helpLabel, children }) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        <h2>{title}{help && <HelpLink chapter={help} label={helpLabel || (typeof title === "string" ? title : help)} />}</h2>
        {description && <p className="sub">{description}</p>}
        {children}
      </div>
      {actions && <div className="page-head-actions">{actions}</div>}
    </header>
  );
}

export function Card({ as: Tag = "div", flat, className, children, ...rest }) {
  return <Tag className={["card", flat && "flat", className].filter(Boolean).join(" ")} {...rest}>{children}</Tag>;
}

export function CardHeader({ title, description, actions }) {
  return (
    <div className="ui-card-head">
      <div><h3>{title}</h3>{description && <p className="sub">{description}</p>}</div>
      {actions && <div className="row" style={{ gap: 8 }}>{actions}</div>}
    </div>
  );
}

// A number tile. tone: blue | amber | violet | green (icon colour), or none.
export function Stat({ label, value, icon, tone, hint, ...rest }) {
  return (
    <div className={["card", "stat", "ui-stat", tone && `tone-${tone}`].filter(Boolean).join(" ")} {...rest}>
      {icon && <span className="stat-icon"><Icon name={icon} /></span>}
      <b>{value}</b><span>{label}</span>{hint && <small className="sub-inline">{hint}</small>}
    </div>
  );
}
export const Stats = ({ children }) => <div className="ui-stats">{children}</div>;

// A slim one-line summary (replaces repeated promo cards).
export function SummaryStrip({ children, actions }) {
  return <div className="ui-strip"><span className="grow">{children}</span>{actions && <span className="actions">{actions}</span>}</div>;
}
