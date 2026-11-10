// Small pieces for speed to lead: the countdown beside a lead waiting for its first touch, and the line in the lead
// drawer that says how the lead got its owner.
import { useEffect, useState } from "react";
import { istLabel } from "../time.js";

export const isWaiting = (lead) => Boolean(lead.first_touch_due_at) && !lead.first_touch_at;

// "12 min left" / "40 min late" / "2 h 5 min late". Ticks every 30 seconds.
export function timerText(dueIso, now = Date.now()) {
  const due = new Date(dueIso).getTime();
  if (Number.isNaN(due)) return "";
  const mins = Math.round(Math.abs(due - now) / 60000);
  const span = mins >= 60 ? `${Math.floor(mins / 60)} h${mins % 60 ? ` ${mins % 60} min` : ""}` : `${mins} min`;
  return due >= now ? `${span} left` : `${span} late`;
}

export function FirstTouchTimer({ lead }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(t); }, []);
  if (lead.first_touch_stuck) return <span className="pill err" title="Moved twice with no first touch. The manager has been told.">Stuck</span>;
  if (!isWaiting(lead)) return <small className="sub">—</small>;
  const late = new Date(lead.first_touch_due_at).getTime() < now;
  return <span className={`pill ${late ? "err" : "warn"}`} data-testid="first-touch-timer" title={`First touch due ${istLabel(lead.first_touch_due_at)}`}>{timerText(lead.first_touch_due_at, now)}</span>;
}

const KINDS = { rule: "a rule", round_robin: "round-robin", upload: "upload", list: "list assignment", manual: "hand", reassigned: "first-touch reassignment" };

export function assignedByText(why) {
  if (!why || !why.kind) return "";
  const how = why.kind === "rule" && why.rule_id ? `rule ${why.rule_id}` : KINDS[why.kind] || why.kind;
  return `Assigned by ${how}${why.by ? ` (${why.by})` : ""}${why.at ? `, ${istLabel(why.at)}` : ""}`;
}

export function LeadAssignedBy({ lead }) {
  const text = assignedByText(lead.owner_why);
  const waiting = isWaiting(lead) || lead.first_touch_stuck;
  if (!text && !waiting) return null;
  return (
    <div className="row" style={{ margin: "6px 0", flexWrap: "wrap", gap: 8 }} data-testid="lead-assigned-by">
      {text && <small className="sub" style={{ margin: 0 }}>{text}</small>}
      {waiting && <><small className="sub" style={{ margin: 0 }}>First touch:</small><FirstTouchTimer lead={lead} /></>}
    </div>
  );
}
