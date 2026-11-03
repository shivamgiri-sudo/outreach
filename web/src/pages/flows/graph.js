// Geometry and structure helpers for the flow canvas: ports, edge curves, auto-layout, the list-view tree, undo.
import { useCallback, useRef, useState } from "react";
import { GRID, NODE_H, NODE_W, PORT_GAP, isTrigger, outputsOf } from "./catalog.js";

export const snap = (v) => Math.round(v / GRID) * GRID;
export const uid = (prefix) => `${prefix}${Math.random().toString(36).slice(2, 8)}`;

export function nodeWidth(node, edges) {
  return Math.max(NODE_W, outputsOf(node, edges).length * PORT_GAP);
}

export function inPort(node, edges) {
  return { x: node.x + nodeWidth(node, edges) / 2, y: node.y };
}

export function outPort(node, label, edges) {
  const outs = outputsOf(node, edges);
  const width = nodeWidth(node, edges);
  const i = Math.max(0, outs.indexOf(label));
  return { x: node.x + (width * (i + 1)) / (outs.length + 1), y: node.y + NODE_H };
}

// A smooth top-to-bottom curve; edges that go back up loop round the side.
export function curve(a, b) {
  const dy = Math.max(50, Math.abs(b.y - a.y) / 2);
  const c1 = { x: a.x, y: a.y + dy };
  const c2 = { x: b.x, y: b.y - dy };
  const mid = { x: (a.x + 3 * c1.x + 3 * c2.x + b.x) / 8, y: (a.y + 3 * c1.y + 3 * c2.y + b.y) / 8 };
  return { d: `M${a.x},${a.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${b.x},${b.y}`, mid };
}

// Top-to-bottom layers from the trigger (longest path, back edges ignored), siblings side by side.
export function autoLayout(nodes, edges) {
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const out = {};
  edges.forEach((e) => { (out[e.from] ||= []).push(e); });
  Object.values(out).forEach((list) => list.sort((a, b) => outputsOf(byId[a.from], edges).indexOf(a.label) - outputsOf(byId[b.from], edges).indexOf(b.label)));
  const start = nodes.find((n) => isTrigger(n.type)) || nodes[0];
  const depth = {};
  const order = [];
  const onStack = new Set();
  const visit = (id, d) => {
    if (onStack.has(id)) return;              // a back edge (loop): ignore for layering
    if (depth[id] !== undefined && depth[id] >= d) return;
    depth[id] = d;
    if (!order.includes(id)) order.push(id);
    onStack.add(id);
    (out[id] || []).forEach((e) => byId[e.to] && visit(e.to, d + 1));
    onStack.delete(id);
  };
  if (start) visit(start.id, 0);
  const looseRow = Math.max(0, ...Object.values(depth)) + 1;   // steps not connected yet go in one row at the bottom
  nodes.forEach((n) => { if (depth[n.id] === undefined) { depth[n.id] = looseRow; order.push(n.id); } });
  const layers = {};
  order.forEach((id) => { (layers[depth[id]] ||= []).push(id); });
  const placed = {};
  Object.keys(layers).map(Number).sort((a, b) => a - b).forEach((d) => {
    const ids = layers[d];
    const parentX = (id) => {
      const ins = edges.filter((e) => e.to === id && placed[e.from]);
      return ins.length ? ins.reduce((s, e) => s + placed[e.from].x, 0) / ins.length : 0;
    };
    ids.sort((a, b) => parentX(a) - parentX(b));
    const gap = NODE_W + 60;
    ids.forEach((id, i) => { placed[id] = { x: snap(400 + (i - (ids.length - 1) / 2) * gap), y: snap(60 + d * 140) }; });
  });
  return nodes.map((n) => ({ ...n, ...(placed[n.id] || {}) }));
}

// The flow as nested steps for the list view: a chain of "next" steps stays flat, branches nest.
export function flowTree(nodes, edges) {
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const seen = new Set();
  const walk = (id) => {
    const steps = [];
    let current = id;
    while (current && byId[current]) {
      if (seen.has(current)) { steps.push({ loopTo: byId[current] }); break; }
      seen.add(current);
      const node = byId[current];
      const outs = outputsOf(node, edges)
        .map((label) => ({ label, to: edges.find((e) => e.from === node.id && e.label === label)?.to }))
        .filter((o) => o.to);
      const branching = outs.length > 1 || (outs.length === 1 && outs[0].label !== "next");
      if (branching) {
        steps.push({ node, branches: outs.map((o) => ({ label: o.label, steps: walk(o.to) })) });
        break;
      }
      steps.push({ node });
      current = outs[0]?.to;
    }
    return steps;
  };
  const start = nodes.find((n) => isTrigger(n.type));
  const steps = start ? walk(start.id) : [];
  const loose = nodes.filter((n) => !seen.has(n.id));
  return { steps, loose };
}

// Undo / redo over {nodes, edges}. Typing in one field is one undo step (same `key` merges).
export function useHistory(initial) {
  const [state, setState] = useState({ past: [], present: initial, future: [] });
  const lastKey = useRef(null);
  const commit = useCallback((next, key = null) => {
    setState((s) => {
      const value = typeof next === "function" ? next(s.present) : next;
      if (key && key === lastKey.current) return { ...s, present: value, future: [] };
      lastKey.current = key;
      return { past: [...s.past.slice(-99), s.present], present: value, future: [] };
    });
  }, []);
  const preview = useCallback((next) => setState((s) => ({ ...s, present: typeof next === "function" ? next(s.present) : next })), []);
  const record = useCallback((before) => {
    lastKey.current = null;
    setState((s) => ({ past: [...s.past.slice(-99), before], present: s.present, future: [] }));
  }, []);
  const undo = useCallback(() => {
    lastKey.current = null;
    setState((s) => (s.past.length ? { past: s.past.slice(0, -1), present: s.past[s.past.length - 1], future: [s.present, ...s.future] } : s));
  }, []);
  const redo = useCallback(() => {
    lastKey.current = null;
    setState((s) => (s.future.length ? { past: [...s.past, s.present], present: s.future[0], future: s.future.slice(1) } : s));
  }, []);
  const reset = useCallback((value) => { lastKey.current = null; setState({ past: [], present: value, future: [] }); }, []);
  return { graph: state.present, commit, preview, record, undo, redo, reset, canUndo: state.past.length > 0, canRedo: state.future.length > 0 };
}
