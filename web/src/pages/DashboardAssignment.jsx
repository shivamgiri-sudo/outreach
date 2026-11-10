// Dashboard card: how fast each person makes a first touch, how many leads were moved away from them, and how many
// leads wait for an owner. Hidden when the server gives nothing (a login that may not see it, or assignment unused).
import { useEffect, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";

export default function DashboardAssignment() {
  const [s, setS] = useState(null);
  const allowed = isOwner() || isWorkspaceAdmin();
  useEffect(() => { if (allowed) api.assignmentStats().then(setS).catch(() => {}); }, [allowed]);
  if (!allowed || !s || (!(s.members || []).length && !s.unassigned && !s.reassigned_total)) return null;
  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="dash-assign" data-testid="dash-assignment">
      <h3 id="dash-assign" style={{ marginTop: 0 }}>First touch by person</h3>
      <p className="sub" style={{ marginTop: 0 }}><b>{s.unassigned ?? 0}</b> lead(s) wait for an owner · <b>{s.reassigned_total ?? 0}</b> moved to someone else for a late first touch.</p>
      {(s.members || []).length > 0 && (
        <div className="table-scroll"><table>
          <thead><tr><th>Person</th><th>Leads given</th><th>Average first touch</th><th>Moved away</th></tr></thead>
          <tbody>{s.members.map((m) => (
            <tr key={m.name}><td>{m.name}</td><td>{m.assigned}</td><td>{m.avg_first_touch_min == null ? "—" : `${Math.round(m.avg_first_touch_min)} min`}</td><td>{m.reassigned_away}</td></tr>))}
          </tbody>
        </table></div>
      )}
    </section>
  );
}
