// Usage against each account limit (server: account_limits.summary). Used on the account's own page and in the console.
const PERIOD = { day: "today", month: "this month", now: "now" };

export default function UsageTable({ rows }) {
  if (!rows?.length) return <p className="hint">No usage yet.</p>;
  return (
    <div className="table-wrap">
      <table className="setup-table" data-testid="usage-table">
        <thead><tr><th scope="col">What</th><th scope="col" className="num">Used</th><th scope="col" className="num">Limit</th><th scope="col">Status</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.kind} data-testid={`usage-${r.kind}`}>
            <td data-label="What">{r.label} <span className="hint">({PERIOD[r.period] || r.period})</span></td>
            <td data-label="Used" className="num">{r.used}</td>
            <td data-label="Limit" className="num">{r.limit ?? "Unlimited"}</td>
            <td data-label="Status">{r.state === "full" ? <b role="status" style={{ color: "#b91c1c" }}>Limit reached ({r.pct}%)</b>
              : r.state === "warn" ? <b style={{ color: "#b45309" }}>{r.pct}% used</b> : r.state === "unlimited" ? <span className="muted">No limit</span> : `${r.pct}% used`}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}
