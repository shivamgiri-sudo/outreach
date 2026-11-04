import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api.js";
import FlowCanvas from "./FlowCanvas.jsx";
import FlowListView from "./FlowListView.jsx";
import NodePicker from "./NodePicker.jsx";
import NodeConfig from "./NodeConfig.jsx";
import { EnrolPanel, RunsPanel, SimulatePanel } from "./FlowPanels.jsx";
import { NODE_H, NODE_W, TYPES, isTrigger, outputsOf, startingConfig, titleOf } from "./catalog.js";
import { autoLayout, snap, uid, useHistory } from "./graph.js";

const PHONE = "(max-width: 600px)";
const isPhone = () => typeof window !== "undefined" && window.matchMedia?.(PHONE).matches;
const typing = (el) => el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
const STATUS = { active: ["ok", "Active"], paused: ["warn", "Paused"], draft: ["", "Draft"] };

export default function FlowEditor({ flowId, catalog, onBack }) {
  const [flow, setFlow] = useState(null);
  const history = useHistory({ nodes: [], edges: [] });
  const { graph, commit, preview, record, undo, redo, reset, canUndo, canRedo } = history;
  const [meta, setMeta] = useState({ name: "", description: "", stop_on_reply: true });
  const [savedKey, setSavedKey] = useState("");
  const [selected, setSelected] = useState(null);
  const [errors, setErrors] = useState([]);
  const [view, setView] = useState(isPhone() ? "list" : "canvas");
  const [note, setNote] = useState({ text: "", bad: false });
  const [sim, setSim] = useState(null);
  const [busy, setBusy] = useState(false);
  const [leads, setLeads] = useState([]);
  const [leaving, setLeaving] = useState(false);
  const [picker, setPicker] = useState(null);                // null, or { from, label, at } where the new step should connect
  const [runsKey, setRunsKey] = useState(0);
  const canvas = useRef(null);
  const config = useRef(null);
  const graphRef = useRef(graph);
  const dragBefore = useRef(null);
  graphRef.current = graph;

  const keyOf = (g, m) => JSON.stringify({ nodes: g.nodes, edges: g.edges, ...m });
  const loaded = (f) => {
    setFlow(f);
    const g = { nodes: f.nodes, edges: f.edges };
    const m = { name: f.name, description: f.description || "", stop_on_reply: f.settings?.stop_on_reply !== false };
    reset(g);
    setMeta(m);
    setSavedKey(keyOf(g, m));
    setErrors(f.errors || []);
  };
  useEffect(() => {
    api.flow(flowId).then((f) => { loaded(f); setTimeout(() => canvas.current?.fit(), 0); })
      .catch((e) => setNote({ text: e.message, bad: true }));
    api.leads().then(setLeads).catch(() => setLeads([]));
  }, [flowId]);

  const dirty = flow && keyOf(graph, meta) !== savedKey;
  const problems = useMemo(() => {
    const out = {};
    errors.forEach((p) => { if (p.node) (out[p.node] ||= []).push(p.message); });
    return out;
  }, [errors]);
  const general = errors.filter((p) => !p.node);
  const highlight = useMemo(() => {
    const ids = new Set();
    if (!sim) return ids;
    sim.path.forEach((p, i) => {
      ids.add(p.node);
      const nextStep = sim.path[i + 1];
      const edge = nextStep && graph.edges.find((e) => e.from === p.node && e.to === nextStep.node && e.label === p.label);
      if (edge) ids.add(edge.id);
    });
    return ids;
  }, [sim, graph.edges]);
  const stepResults = useMemo(() => Object.fromEntries((sim?.path || []).map((p) => [p.node, { ok: p.ok !== false, text: p.result }])), [sim]);
  const selectedNode = selected?.kind === "node" ? graph.nodes.find((n) => n.id === selected.id) : null;
  const say = (text, bad = false) => setNote({ text, bad });

  const addNode = (type, x, y, from = null) => {
    const id = uid("n");
    let pos = { x, y };
    if (x === undefined) {
      const centre = view === "canvas" && canvas.current ? canvas.current.centre() : null;
      const lowest = Math.max(0, ...graphRef.current.nodes.map((n) => n.y));
      pos = centre ? { x: snap(centre.x - 110 + (graphRef.current.nodes.length % 4) * 20), y: snap(centre.y) } : { x: 300, y: snap(lowest + NODE_H + 70) };
    }
    if (from) {
      const source = graphRef.current.nodes.find((n) => n.id === from.id);
      const slot = source ? Math.max(0, outputsOf(source, graphRef.current.edges).indexOf(from.label)) : 0;
      if (source && x === undefined) pos = from.at ? { x: snap(from.at.x - 110), y: snap(from.at.y + 20) } : { x: snap(source.x + slot * (NODE_W + 40)), y: snap(source.y + NODE_H + 100) };
    }
    commit((g) => ({
      ...g,
      nodes: [...g.nodes, { id, type, config: startingConfig(type), ...pos }],
      edges: from ? [...g.edges.filter((e) => !(e.from === from.id && e.label === from.label)), { id: uid("e"), from: from.id, to: id, label: from.label }] : g.edges,
    }));
    setSelected({ kind: "node", id });
    say(`Added “${TYPES[type][1]}”. ${isTrigger(type) ? "" : "Connect it by dragging from an output dot, or pick where paths go in the panel."}`);
  };
  const addFrom = (nodeId, label, at) => setPicker({ id: nodeId, label, at });
  const duplicate = (id) => {
    const original = graphRef.current.nodes.find((n) => n.id === id);
    if (!original || isTrigger(original.type)) return;
    const copyId = uid("n");
    commit((g) => ({ ...g, nodes: [...g.nodes, { ...JSON.parse(JSON.stringify(original)), id: copyId, x: original.x + 40, y: original.y + 40,
      config: { ...JSON.parse(JSON.stringify(original.config || {})), title: `${titleOf(original)} (copy)` } }] }));
    setSelected({ kind: "node", id: copyId });
    say("Duplicated. The copy is not connected yet.");
  };
  const toggleOff = (id) => {
    const target = graphRef.current.nodes.find((n) => n.id === id);
    if (!target || isTrigger(target.type) || ["stop", "note"].includes(target.type)) return;
    commit((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, config: { ...n.config, disabled: !n.config?.disabled } } : n)) }), `${id}:disabled`);
    say(target.config?.disabled ? `${titleOf(target)} is on again.` : `${titleOf(target)} is turned off: leads skip it.`);
  };
  const connect = (from, label, to) => commit((g) => ({
    ...g, edges: [...g.edges.filter((e) => !(e.from === from && e.label === label)), { id: uid("e"), from, to, label }],
  }));
  const unlink = (from, label) => commit((g) => ({ ...g, edges: g.edges.filter((e) => !(e.from === from && e.label === label)) }));
  const deleteEdge = (id) => { commit((g) => ({ ...g, edges: g.edges.filter((e) => e.id !== id) })); setSelected(null); };
  const deleteNode = (id) => {
    commit((g) => ({ nodes: g.nodes.filter((n) => n.id !== id), edges: g.edges.filter((e) => e.from !== id && e.to !== id) }));
    setSelected(null);
  };
  const updateConfig = (patch, key) => commit((g) => ({
    ...g, nodes: g.nodes.map((n) => (n.id === selected?.id ? { ...n, config: { ...n.config, ...patch } } : n)),
  }), key);
  const moveBy = (id, dx, dy) => commit((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, x: n.x + dx, y: n.y + dy } : n)) }));
  const openConfig = (id) => {
    setSelected({ kind: "node", id });
    setTimeout(() => config.current?.querySelector("input, select, textarea")?.focus(), 0);
  };
  const layout = () => {
    commit((g) => ({ ...g, nodes: autoLayout(g.nodes, g.edges) }));
    setTimeout(() => canvas.current?.fit(), 0);
    say("Laid out from top to bottom.");
  };

  useEffect(() => {
    const onKey = (e) => {
      if (typing(e.target) || e.defaultPrevented) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
      else if (mod && e.key.toLowerCase() === "d" && selected?.kind === "node") { e.preventDefault(); duplicate(selected.id); }
      else if ((e.key === "Delete" || e.key === "Backspace") && selected
               && (e.target === document.body || e.target.closest?.(".fb-stage"))) {
        e.preventDefault();
        selected.kind === "edge" ? deleteEdge(selected.id) : deleteNode(selected.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, undo, redo]);

  const body = () => ({ name: meta.name, description: meta.description, settings: { stop_on_reply: meta.stop_on_reply },
    nodes: graph.nodes, edges: graph.edges });
  const save = async () => {
    setBusy(true);
    try {
      const saved = await api.saveFlow(flowId, body());
      const versionChanged = flow && saved.version !== flow.version;
      setFlow(saved);
      setSavedKey(keyOf(graph, meta));
      setErrors(saved.errors || []);
      say(versionChanged ? `Saved as version ${saved.version}. Leads already in the flow finish on their version.` : "Saved.");
      return saved;
    } catch (e) { say(e.message, true); return null; } finally { setBusy(false); }
  };
  const validate = async () => {
    try {
      const r = await api.validateFlow(flowId, { nodes: graph.nodes, edges: graph.edges });
      setErrors(r.errors);
      say(r.ok ? "No problems found. Ready to activate." : `${r.errors.length} problem(s) to fix: shown in red on the steps.`, !r.ok);
    } catch (e) { say(e.message, true); }
  };
  const activate = async () => {
    if (dirty && !(await save())) return;
    try {
      const f = await api.activateFlow(flowId);
      setFlow(f);
      setErrors(f.errors || []);
      say("Active. Leads that match the trigger start now; steps run every minute (with OUTREACH_AUTORUN=1).");
    } catch (e) { say(e.message, true); validate(); }
  };
  const pause = async () => {
    try { setFlow(await api.pauseFlow(flowId)); say("Paused. Leads stay where they are until you activate it again."); }
    catch (e) { say(e.message, true); }
  };
  const simulate = async (leadId, assume) => {
    if (dirty && !(await save())) return;
    setBusy(true);
    try {
      const r = await api.simulateFlow(flowId, leadId, assume);
      setSim(r);
      say(`Simulation: ${r.path.length} step(s). ${r.ended}`);
    } catch (e) { say(e.message, true); } finally { setBusy(false); }
  };

  const exportFlow = async () => {
    try {
      const data = await api.exportFlow(flowId);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = `${(data.name || "flow").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.json`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      say(dirty ? "Exported the last saved version. Save first to include your latest changes." : "Exported.");
    } catch (e) { say(e.message, true); }
  };
  if (!flow) return <p className="empty">{note.text || "Loading…"}</p>;
  const [pill, word] = STATUS[flow.status] || ["", flow.status];

  return (
    <div className="fb-editor">
      <h2 className="sr-only">Flow builder: {meta.name}</h2>
      <div className="fb-top">
        <button type="button" className="btn ghost" onClick={() => (dirty ? setLeaving(true) : onBack())}>← All flows</button>
        <label className="fb-name" htmlFor="fb-name">
          <span className="sr-only">Flow name</span>
          <input id="fb-name" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} />
        </label>
        <span className={`pill ${pill}`} data-testid="flow-status">{word}</span>
        <span className="sub-inline">v{flow.version}{dirty ? " · unsaved changes" : ""}</span>
      </div>
      {leaving && (
        <div className="tip error" role="alert">You have unsaved changes.
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="btn" onClick={async () => { if (await save()) onBack(); }}>Save and leave</button>
            <button type="button" className="btn ghost" onClick={onBack}>Leave without saving</button>
            <button type="button" className="btn ghost" onClick={() => setLeaving(false)}>Stay</button>
          </div>
        </div>
      )}
      <div className="fb-bar" role="toolbar" aria-label="Flow actions">
        <button type="button" className="btn" onClick={() => setPicker({ id: null, label: null, at: null })} data-testid="add-step"><b aria-hidden="true">+</b> Add step</button>
        <button type="button" className="btn ghost" onClick={undo} disabled={!canUndo} aria-keyshortcuts="Control+Z">Undo</button>
        <button type="button" className="btn ghost" onClick={redo} disabled={!canRedo} aria-keyshortcuts="Control+Y">Redo</button>
        <button type="button" className="btn ghost" onClick={layout}>Auto-layout</button>
        <button type="button" className="btn ghost" aria-pressed={view === "list"} onClick={() => setView(view === "list" ? "canvas" : "list")}>
          {view === "list" ? "Canvas view" : "List view"}
        </button>
        <span className="fb-spacer" />
        <button type="button" className="btn ghost" onClick={exportFlow}>Export</button>
        <button type="button" className="btn ghost" onClick={validate}>Validate</button>
        <button type="button" className="btn" onClick={save} disabled={busy || !dirty}>Save</button>
        {flow.status === "active"
          ? <button type="button" className="btn ghost" onClick={pause}>Pause</button>
          : <button type="button" className="btn cta" onClick={activate}>Activate</button>}
      </div>
      {note.text && <div className={`tip${note.bad ? " error" : ""}`} role={note.bad ? "alert" : "status"}>{note.text}</div>}
      {general.length > 0 && <div className="tip error" role="alert">{general.map((p, i) => <div key={i}>{p.message}</div>)}</div>}
      <div className="fb-main">
        <div className="fb-stage">
          {view === "canvas" ? (
            <>
              <p className="field-help fb-help">Drag the background to move around, scroll to zoom. Drag from a dot under a step to another step to connect, or click the + under it to add the next step.
                Keyboard: Tab to a step, arrows move it, Enter edits, D turns it off, Ctrl+D duplicates, Delete removes; Ctrl+Z / Ctrl+Y undo and redo.</p>
              <FlowCanvas ref={canvas} nodes={graph.nodes} edges={graph.edges} selected={selected} problems={problems} highlight={highlight}
                caller={catalog?.caller} onSelect={setSelected} onDragStart={() => { dragBefore.current = graphRef.current; }}
                onPreviewMove={(id, x, y) => preview((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) }))}
                onCommitMove={() => dragBefore.current && record(dragBefore.current)} onKeyMove={moveBy} onConnect={connect}
                onDeleteEdge={deleteEdge} onDeleteNode={deleteNode} onOpenConfig={openConfig} onDropType={addNode}
                onAddFrom={addFrom} onDuplicate={duplicate} onToggleOff={toggleOff} stepResults={stepResults} />
            </>
          ) : (
            <FlowListView nodes={graph.nodes} edges={graph.edges} problems={problems} highlight={highlight} caller={catalog?.caller} onEdit={openConfig} />
          )}
        </div>
        <div className="fb-side">
          {selectedNode ? (
            <NodeConfig key={selectedNode.id} flowId={flowId} node={selectedNode} nodes={graph.nodes} edges={graph.edges} catalog={catalog}
              problems={problems[selectedNode.id] || []} onChange={updateConfig} panelRef={config}
              onConnect={(label, to) => (to ? connect(selectedNode.id, label, to) : unlink(selectedNode.id, label))}
              onDelete={() => deleteNode(selectedNode.id)} onDuplicate={() => duplicate(selectedNode.id)} />
          ) : (
            <section className="card fb-panel">
              <h3>Flow settings</h3>
              <label htmlFor="fb-desc">Description
                <textarea id="fb-desc" rows={3} value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
              </label>
              <label className="check-row"><input type="checkbox" checked={meta.stop_on_reply} onChange={(e) => setMeta({ ...meta, stop_on_reply: e.target.checked })} />
                Stop for a lead as soon as they reply (except at a “Wait for a reply” step). Recommended for cold flows.</label>
              <p className="field-help">Select a step to change it. {graph.nodes.length} step(s), {graph.edges.length} connection(s).</p>
            </section>
          )}
          <SimulatePanel leads={leads} result={sim} busy={busy} onRun={simulate} onClear={() => setSim(null)} />
          <EnrolPanel flow={flow} leads={leads} onDone={() => setRunsKey((k) => k + 1)} />
          <RunsPanel flowId={flowId} refreshKey={`${runsKey}-${flow.status}`} />
        </div>
      </div>
      <NodePicker open={!!picker} onlyTriggers={false} heading={picker?.id ? "What happens next?" : "Add a step"}
        onClose={() => setPicker(null)}
        onPick={(type) => { const from = picker?.id ? picker : null; addNode(type, undefined, undefined, from); }} />
      <p className="sr-only" aria-live="polite">{selectedNode ? `Selected ${titleOf(selectedNode)}` : ""}</p>
    </div>
  );
}
