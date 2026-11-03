import { BRANCH_HEADINGS, labelOf, summaryOf, titleOf } from "./catalog.js";
import { flowTree } from "./graph.js";

// The flow as nested steps: for screen readers, keyboards and phones. Each step can be opened for editing.
function Steps({ steps, problems, highlight, caller, onEdit }) {
  return (
    <ol className="fb-steps">
      {steps.map((s, i) => {
        if (s.loopTo) {
          return <li key={`loop-${i}`} className="fb-step loop">Goes back to “{titleOf(s.loopTo)}”</li>;
        }
        const issues = problems[s.node.id] || [];
        return (
          <li key={s.node.id} className={`fb-step${issues.length ? " bad" : ""}${highlight.has(s.node.id) ? " on" : ""}`} data-step-id={s.node.id}>
            <div className="fb-step-head">
              <span><b>{titleOf(s.node)}</b> <span className="sub-inline">· {labelOf(s.node.type)}</span></span>
              <button type="button" className="btn ghost" onClick={() => onEdit(s.node.id)} aria-label={`Edit ${titleOf(s.node)}`}>Edit</button>
            </div>
            <div className="fb-step-sum">{summaryOf(s.node, caller)}</div>
            {issues.map((p, j) => <div key={j} className="fb-step-err" role="note">⚠ {p}</div>)}
            {s.branches && (
              <ul className="fb-branches">
                {s.branches.map((b) => (
                  <li key={b.label}>
                    <span className="fb-branch-label">{BRANCH_HEADINGS[b.label] || b.label}:</span>
                    <Steps steps={b.steps} problems={problems} highlight={highlight} caller={caller} onEdit={onEdit} />
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export default function FlowListView({ nodes, edges, problems, highlight, caller, onEdit }) {
  const { steps, loose } = flowTree(nodes, edges);
  return (
    <div className="fb-list card" data-testid="flow-list-view">
      <h3 style={{ marginTop: 0 }}>Steps</h3>
      {steps.length === 0 ? <p className="sub">Add a trigger to start the flow.</p>
        : <Steps steps={steps} problems={problems} highlight={highlight} caller={caller} onEdit={onEdit} />}
      {loose.length > 0 && (
        <>
          <h4>Not connected yet</h4>
          <ul className="fb-steps loose">
            {loose.map((n) => (
              <li key={n.id} className={`fb-step${(problems[n.id] || []).length ? " bad" : ""}`}>
                <div className="fb-step-head">
                  <span><b>{titleOf(n)}</b> <span className="sub-inline">· {labelOf(n.type)}</span></span>
                  <button type="button" className="btn ghost" onClick={() => onEdit(n.id)} aria-label={`Edit ${titleOf(n)}`}>Edit</button>
                </div>
                <div className="fb-step-sum">{summaryOf(n, caller)}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
