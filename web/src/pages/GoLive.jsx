import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";
import { GuideButton } from "../GuidePanel.jsx";
import { Button, PageHeader } from "../ui/index.js";

const MARK = { done: "✓", todo: "○", optional: "–" };
const PILL = { done: "ok", todo: "warn", optional: "" };

export default function GoLive({ go, embedded = false }) {
  const [r, setR] = useState(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); api.readiness().then(setR).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message="The checklist could not load." detail={error} onRetry={load} />;
  if (!r) return <p className="empty">Loading…</p>;

  const required = r.items.filter((i) => i.required);
  const requiredDone = required.filter((i) => i.status === "done").length;
  const sections = [
    ["Needed before a live test", r.items.filter((i) => i.required)],
    ["The last switch", r.items.filter((i) => i.id === "live_sending")],
    ["Recommended and optional", r.items.filter((i) => !i.required && i.id !== "live_sending")],
  ];

  return (
    <>
      {embedded ? null : <PageHeader title="Go live" description="Everything needed to move from practice mode to real sending, in order." />}
      <div className="tip">
        {r.ready_for_live_test
          ? <><b>Ready for a live test.</b> Send to your own address first, then switch on real sending.</>
          : <><b>{requiredDone} of {required.length} required steps done.</b> Finish the rest to start a live test.</>}
        <div className="bar progress-bar" style={{ width: `${(requiredDone / required.length) * 100}%` }} />
      </div>

      {sections.map(([title, items]) => (
        <div key={title} className="check-section">
          <h3>{title}</h3>
          {items.map((i) => (
            <div className="card check-item" key={i.id}>
              <span className="check-text">
                <b>{MARK[i.status]} {i.title}</b><br /><small className="sub">{i.detail}</small>
              </span>
              <span className="row">
                <span className={`pill ${PILL[i.status]}`}>{i.status === "done" ? "Done" : i.status === "todo" ? "To do" : "Optional"}</span>
                {i.status !== "done" && <GuideButton keys={`setup:${i.id}`} label="Need help?" testId={`checklist-guide-${i.id}`} />}
                {i.status !== "done" && <Button variant="secondary" size="sm" onClick={() => go(i.page)}>Fix this</Button>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
