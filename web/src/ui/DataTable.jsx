// A scroll container for a table: sticky header, row hover, horizontal scroll. Pass real <thead>/<tbody> as children.
// maxHeight (css length) makes the header stick inside the box; density="dense" tightens rows.
export function DataTable({ children, maxHeight, density, className = "", tableClass = "", label, ...rest }) {
  return (
    <div className={`ui-table-wrap ${className}`} style={maxHeight ? { "--table-max": maxHeight } : undefined} role="region" aria-label={label} tabIndex={0}>
      <table className={`ui-table ${density === "dense" ? "dense" : ""} ${tableClass}`} {...rest}>{children}</table>
    </div>
  );
}

// Two-line cell: strong first line, muted second line, both truncated with the full text as a tooltip.
export function Cell({ title, sub, children }) {
  return (
    <div className="ui-cell">
      <b title={typeof title === "string" ? title : undefined}>{title}</b>
      {sub ? <small title={typeof sub === "string" ? sub : undefined}>{sub}</small> : null}{children}
    </div>
  );
}
