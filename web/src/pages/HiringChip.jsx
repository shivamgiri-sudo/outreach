import { hiringRoles } from "./leadFilters.js";
import { Badge } from "../ui/index.js";

// "Hiring: N roles" on a lead row; the role titles show on hover (and to screen readers through the label).
export default function HiringChip({ lead }) {
  const n = hiringRoles(lead);
  if (!n) return null;
  const titles = lead.hiring_signal?.titles || [];
  const tip = titles.length ? `Open roles: ${titles.join(", ")}${n > titles.length ? ` and ${n - titles.length} more` : ""}` : "Open roles from a job list";
  return <Badge tone="hot" className="hiring-chip" title={tip} aria-label={`Hiring: ${n} open roles. ${tip}`} data-testid="hiring-chip">Hiring: {n} {n === 1 ? "role" : "roles"}</Badge>;
}
