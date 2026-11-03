import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";

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
      {embedded ? <h3>Go-live checklist</h3> : <h2>Go live</h2>}
      <p className="sub">Everything needed to move from practice mode to real sending, in order.</p>
      <div className={`tip`} style={{ marginBottom: 14 }}>
        {r.ready_for_live_test
          ? <><b>Ready for a live test.</b> Send to your own address first, then switch on real sending.</>
          : <><b>{requiredDone} of {required.length} required steps done.</b> Finish the rest to start a live test.</>}
        <div className="bar" style={{ width: `${(requiredDone / required.length) * 100}%`, marginTop: 8, height: 8 }} />
      </div>

      {sections.map(([title, items]) => (
        <div key={title}>
          <h3>{title}</h3>
          {items.map((i) => (
            <div className="card row" key={i.id} style={{ marginBottom: 8, justifyContent: "space-between" }}>
              <span style={{ flex: 1, minWidth: 220 }}>
                <b>{MARK[i.status]} {i.title}</b><br /><small className="sub">{i.detail}</small>
              </span>
              <span className="row">
                <span className={`pill ${PILL[i.status]}`}>{i.status === "done" ? "Done" : i.status === "todo" ? "To do" : "Optional"}</span>
                {i.status !== "done" && <button className="btn ghost" onClick={() => go(i.page)}>Fix this</button>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
