// The leads behind one number on the Sales funnel page: ID, company, owner, stage and value. The row count equals the number.
import { useEffect, useRef, useState } from "react";
import DrawerShell from "../DrawerShell.jsx";
import { sales } from "../salesApi.js";
import { inr } from "../salesFormat.js";

export default function SalesDrill({ drillKey, title, filters, onClose }) {
  const [state, setState] = useState({ loading: true });
  const ref = useRef(0);
  useEffect(() => {
    const mine = ++ref.current;
    setState({ loading: true });
    sales.drill(drillKey, filters).then((r) => mine === ref.current && setState({ ...r }))
      .catch((e) => mine === ref.current && setState({ error: e.message }));
  }, [drillKey]);
  return (
    <DrawerShell onClose={onClose} labelledBy="sf-drill-title" testId="sf-drill">
      <div className="drawer-head">
        <div>
          <h2 id="sf-drill-title" tabIndex={-1}>{title}</h2>
          <p className="sub" style={{ margin: 0 }}>{state.loading ? "Loading…" : state.error ? "" : <><b data-testid="sf-drill-count">{state.count}</b> lead{state.count === 1 ? "" : "s"}{state.truncated ? " (first 10,000 shown)" : ""}</>}</p>
        </div>
        <button className="btn ghost" onClick={onClose} aria-label="Close the list">Close</button>
      </div>
      {state.error && <div className="tip error" role="alert">{state.error}</div>}
      {state.rows && (state.rows.length === 0 ? <p className="empty">No leads behind this number.</p> : (
        <div className="table-card" style={{ overflowX: "auto" }}>
          <table data-testid="sf-drill-table">
            <thead><tr><th>ID</th><th>Company</th><th>Owner</th><th>Stage</th><th style={{ textAlign: "right" }}>Value / month</th></tr></thead>
            <tbody>
              {state.rows.map((r) => (
                <tr key={r.id} data-testid="sf-drill-row">
                  <td><a href={`#leads?lead=${encodeURIComponent(r.id)}`} onClick={onClose}>{r.id}</a></td>
                  <td>{r.company || "—"}</td><td>{r.owner || "—"}</td><td>{r.stage}</td>
                  <td style={{ textAlign: "right" }}>{r.value ? inr(r.value) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </DrawerShell>
  );
}
