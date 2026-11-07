import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from "react";
import { GRID, NODE_H, NODE_W, groupOf, iconOf, iconPath, isTrigger, outputLabel, outputsOf, summaryOf, titleOf } from "./catalog.js";
import { curve, inPort, nodeWidth, outPort, snap } from "./graph.js";

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2;
const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
const clamp = (v) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v));

// The drawing surface: drag the background to pan, wheel or buttons to zoom, drag a card to move it, drag from an
// output port to another card to connect them. Cards and connections are focusable buttons for keyboard users.
const FlowCanvas = forwardRef(function FlowCanvas(props, ref) {
  const { nodes, edges, selected, problems, highlight, caller, onSelect, onDragStart, onPreviewMove, onCommitMove,
    onKeyMove, onConnect, onDeleteEdge, onDeleteNode, onOpenConfig, onDropType, onAddFrom, onDuplicate, onToggleOff, stepResults, readOnly } = props;
  const svg = useRef(null);
  const [view, setView] = useState({ x: 40, y: 20, k: 1 });
  const [drag, setDrag] = useState(null);
  const [pointer, setPointer] = useState(null);
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  const rect = () => svg.current?.getBoundingClientRect() || { left: 0, top: 0, width: 800, height: 500 };
  const toCanvas = (clientX, clientY, v = view) => {
    const r = rect();
    return { x: (clientX - r.left - v.x) / v.k, y: (clientY - r.top - v.y) / v.k };
  };
  const zoomAt = (factor, cx, cy) => setView((v) => {
    const k = clamp(v.k * factor);
    return { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k };
  });
  const fit = () => {
    if (!nodes.length) return;
    const r = rect();
    const xs = nodes.map((n) => n.x);
    const ys = nodes.map((n) => n.y);
    const minX = Math.min(...xs) - 40;
    const minY = Math.min(...ys) - 30;
    const width = Math.max(...nodes.map((n) => n.x + nodeWidth(n, edges))) - minX + 40;
    const height = Math.max(...ys) + NODE_H + 70 - minY;
    const k = clamp(Math.min(1, r.width / width, r.height / height));
    setView({ k, x: (r.width - width * k) / 2 - minX * k, y: 16 - minY * k });
  };
  useImperativeHandle(ref, () => ({
    centre: () => { const r = rect(); return toCanvas(r.left + r.width / 2, r.top + r.height / 3); },
    fit,
    // Pan just enough that a card at canvas point p is on screen (a step added after the 4th output landed off to the right).
    reveal: (p) => setView((v) => {
      const r = rect();
      const pad = 24;
      const left = p.x * v.k + v.x, top = p.y * v.k + v.y, w = NODE_W * v.k, h = (NODE_H + 90) * v.k;   // + its outputs and + buttons, clear of the zoom bar
      const dx = left < pad ? pad - left : left + w > r.width - pad ? r.width - pad - (left + w) : 0;
      const dy = top < pad ? pad - top : top + h > r.height - pad ? r.height - pad - (top + h) : 0;
      return dx || dy ? { ...v, x: v.x + dx, y: v.y + dy } : v;
    }),
  }));

  useEffect(() => {
    const el = svg.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const startPan = (e) => {
    if (e.button !== 0) return;
    svg.current.setPointerCapture?.(e.pointerId);
    setDrag({ mode: "pan", sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y });
    onSelect(null);
  };
  const startNode = (e, node) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    svg.current.setPointerCapture?.(e.pointerId);
    onSelect({ kind: "node", id: node.id });
    const p = toCanvas(e.clientX, e.clientY);
    setDrag({ mode: "node", id: node.id, dx: p.x - node.x, dy: p.y - node.y, moved: false });
  };
  const startConnect = (e, node, label) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    svg.current.setPointerCapture?.(e.pointerId);
    setDrag({ mode: "connect", from: node.id, label, start: outPort(node, label, edges) });
    setPointer(toCanvas(e.clientX, e.clientY));
  };
  const move = (e) => {
    if (!drag) return;
    if (drag.mode === "pan") setView((v) => ({ ...v, x: drag.vx + e.clientX - drag.sx, y: drag.vy + e.clientY - drag.sy }));
    if (drag.mode === "node") {
      const p = toCanvas(e.clientX, e.clientY);
      if (!drag.moved) { onDragStart(); setDrag((d) => ({ ...d, moved: true })); }
      onPreviewMove(drag.id, snap(p.x - drag.dx), snap(p.y - drag.dy));
    }
    if (drag.mode === "connect") setPointer(toCanvas(e.clientX, e.clientY));
  };
  const end = (e) => {
    if (!drag) return;
    if (drag.mode === "node" && drag.moved) onCommitMove();
    if (drag.mode === "connect") {
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-node-id]");
      const to = target?.getAttribute("data-node-id");
      if (to && to !== drag.from) onConnect(drag.from, drag.label, to);
      else if (!to && pointer && Math.hypot(pointer.x - drag.start.x, pointer.y - drag.start.y) > 40) onAddFrom?.(drag.from, drag.label, pointer);
    }
    setDrag(null);
    setPointer(null);
  };

  const keyNode = (e, node) => {
    const step = e.shiftKey ? GRID * 5 : GRID;
    const moves = { ArrowUp: [0, -step], ArrowDown: [0, step], ArrowLeft: [-step, 0], ArrowRight: [step, 0] };
    if (moves[e.key]) { e.preventDefault(); onKeyMove(node.id, ...moves[e.key]); }
    else if (e.key === "Enter") { e.preventDefault(); onOpenConfig(node.id); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") { e.preventDefault(); onDuplicate?.(node.id); }
    else if (e.key.toLowerCase() === "d" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onToggleOff?.(node.id); }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); onDeleteNode(node.id); }
  };
  const keyEdge = (e, edge) => {
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); onDeleteEdge(edge.id); }
  };
  const onDrop = (e) => {
    const type = e.dataTransfer.getData("application/x-flow-step");
    if (!type) return;
    e.preventDefault();
    const p = toCanvas(e.clientX, e.clientY);
    onDropType(type, snap(p.x - NODE_W / 2), snap(p.y - NODE_H / 2));
  };

  return (
    <div className="fb-canvas" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <svg ref={svg} role="group" aria-label="Flow canvas" data-testid="flow-canvas"
        onPointerDown={startPan} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        <defs>
          <pattern id="fb-grid" width={GRID} height={GRID} patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" className="fb-grid-dot" /></pattern>
          <marker id="fb-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="fb-arrow" />
          </marker>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          <rect x="-5000" y="-5000" width="10000" height="10000" fill="url(#fb-grid)" />
          {edges.map((e) => {
            const a = byId[e.from];
            const b = byId[e.to];
            if (!a || !b) return null;
            const { d, mid } = curve(outPort(a, e.label, edges), inPort(b, edges));
            const isSel = selected?.kind === "edge" && selected.id === e.id;
            const label = outputLabel(a, e.label);
            return (
              <g key={e.id} className={`fb-edge${highlight.has(e.id) ? " on" : ""}${isSel ? " sel" : ""}${e.label === "error" ? " err" : ""}`}
                data-edge-id={e.id} tabIndex={0} role="button" aria-pressed={isSel}
                aria-label={`Connection from ${titleOf(a)}${label ? ` (${label})` : ""} to ${titleOf(b)}. Press Delete to remove.`}
                onPointerDown={(ev) => { ev.stopPropagation(); onSelect({ kind: "edge", id: e.id }); }}
                onFocus={() => onSelect({ kind: "edge", id: e.id })} onKeyDown={(ev) => keyEdge(ev, e)}>
                <path d={d} className="fb-edge-hit" />
                <path d={d} className="fb-edge-line" markerEnd="url(#fb-arrow)" />
                {label && <text x={mid.x} y={mid.y - 6} className="fb-edge-label">{label}</text>}
                {isSel && !readOnly && (
                  <g className="fb-edge-x" transform={`translate(${mid.x},${mid.y + 10})`} role="button" aria-label="Remove this connection"
                    onPointerDown={(ev) => { ev.stopPropagation(); onDeleteEdge(e.id); }}>
                    <circle r="11" /><path d="M-4,-4 L4,4 M4,-4 L-4,4" />
                  </g>
                )}
              </g>
            );
          })}
          {drag?.mode === "connect" && pointer && <path className="fb-edge-temp" d={curve(drag.start, pointer).d} />}
          {nodes.map((n) => {
            if (n.type === "note") {
              const sel = selected?.kind === "node" && selected.id === n.id;
              return (
                <g key={n.id} data-node-id={n.id} className={`fb-note${sel ? " sel" : ""}`} transform={`translate(${n.x},${n.y})`} tabIndex={0} role="button"
                  aria-label={`Note: ${n.config?.text || "empty"}. Arrow keys move it, Enter edits, Delete removes.`}
                  onPointerDown={(e) => startNode(e, n)} onFocus={() => onSelect({ kind: "node", id: n.id })}
                  onKeyDown={(e) => keyNode(e, n)} onDoubleClick={() => onOpenConfig(n.id)}>
                  <rect className="fb-note-card" width="240" height="120" rx="6" />
                  <foreignObject x="10" y="8" width="220" height="104">
                    <div xmlns="http://www.w3.org/1999/xhtml" className="fb-note-text">
                      {n.config?.title ? <b>{n.config.title}</b> : null}{n.config?.text || "Double-click to write a note"}
                    </div>
                  </foreignObject>
                </g>
              );
            }
            const width = nodeWidth(n, edges);
            const outs = outputsOf(n, edges);
            const issues = problems[n.id] || [];
            const isSel = selected?.kind === "node" && selected.id === n.id;
            const summary = summaryOf(n, caller);
            const off = !!n.config?.disabled;
            const result = stepResults?.[n.id];
            const cls = `fb-node g-${groupOf(n.type)}${isSel ? " sel" : ""}${issues.length ? " bad" : ""}${highlight.has(n.id) ? " on" : ""}${off ? " off" : ""}`;
            return (
              <g key={n.id} data-node-id={n.id} className={cls} transform={`translate(${n.x},${n.y})`} tabIndex={0} role="button"
                aria-pressed={isSel} aria-label={`${titleOf(n)}: ${summary}${issues.length ? `. Problem: ${issues.join(" ").replace(/\.+$/, "")}` : ""}. Arrow keys move it, Enter edits, Delete removes.`}
                onPointerDown={(e) => startNode(e, n)} onFocus={() => onSelect({ kind: "node", id: n.id })}
                onKeyDown={(e) => keyNode(e, n)} onDoubleClick={() => onOpenConfig(n.id)}>
                <title>{[titleOf(n), summary, ...issues].join(" — ")}</title>
                <rect className="fb-card" width={width} height={NODE_H} rx="12" />
                <rect className="fb-band" width="6" height={NODE_H} rx="3" />
                <g transform="translate(16,11) scale(.75)"><path d={iconPath(iconOf(n.type))} className="fb-icon" /></g>
                <text x="44" y="27" className="fb-title">{clip(titleOf(n), Math.floor(width / 9))}{off ? " (off)" : ""}</text>
                {result && <g className={`fb-result ${result.ok === false ? "bad" : "ok"}`} transform={`translate(${width - 10},-8)`}>
                  <circle r="11" /><path d={result.ok === false ? "M-4,-4 L4,4 M4,-4 L-4,4" : "M-5,0 L-1.5,4 L5,-4"} />
                </g>}
                {issues.length
                  ? <text x="16" y="52" className="fb-err-text" data-testid="node-error">{clip(`⚠ ${issues[0]}`, Math.floor(width / 6.4))}</text>
                  : <text x="16" y="52" className="fb-summary">{clip(summary, Math.floor(width / 6.4))}</text>}
                {!isTrigger(n.type) && <circle className="fb-port in" cx={width / 2} cy="0" r="6" />}
                {outs.map((label, i) => {
                  const px = (width * (i + 1)) / (outs.length + 1);
                  return (
                    <g key={label} className={`fb-port-wrap${label === "error" ? " err" : ""}`} data-port={`${n.id}:${label}`}
                      onPointerDown={(e) => startConnect(e, n, label)}>
                      <circle className="fb-port-hit" cx={px} cy={NODE_H} r="13" />
                      <circle className="fb-port out" cx={px} cy={NODE_H} r="7" />
                      {outputLabel(n, label) && !edges.some((e) => e.from === n.id && e.label === label) && <text className="fb-port-label" x={px} y={NODE_H + 22}>{clip(outputLabel(n, label), 12)}</text>}
                    </g>
                  );
                })}
                {outs.map((label, i) => {
                  if (readOnly || edges.some((e) => e.from === n.id && e.label === label)) return null;
                  const px = (width * (i + 1)) / (outs.length + 1);
                  return (
                    <g key={`add-${label}`} className="fb-add" transform={`translate(${px},${NODE_H + 44})`} role="button" tabIndex={0}
                      aria-label={`Add a step after ${titleOf(n)}${outputLabel(n, label) ? ` (${outputLabel(n, label)})` : ""}`}
                      onPointerDown={(e) => { e.stopPropagation(); onAddFrom?.(n.id, label, null); }}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onAddFrom?.(n.id, label, null); } }}>
                      <line x1="0" y1="-30" x2="0" y2="-12" className="fb-add-line" />
                      <rect x="-12" y="-12" width="24" height="24" rx="6" />
                      <path d="M0,-5 L0,5 M-5,0 L5,0" />
                    </g>
                  );
                })}
              </g>
            );
          })}
        </g>
      </svg>
      <Minimap nodes={nodes} edges={edges} view={view} size={rect()} onJump={(cx, cy) => { const r = rect(); setView((v) => ({ ...v, x: r.width / 2 - cx * v.k, y: r.height / 2 - cy * v.k })); }} />
      <div className="fb-zoom" role="toolbar" aria-label="Zoom">
        <button type="button" className="btn ghost" aria-label="Zoom out" onClick={() => { const r = rect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2); }}>−</button>
        <span aria-live="polite">{Math.round(view.k * 100)}%</span>
        <button type="button" className="btn ghost" aria-label="Zoom in" onClick={() => { const r = rect(); zoomAt(1.2, r.width / 2, r.height / 2); }}>+</button>
        <button type="button" className="btn ghost" onClick={fit}>Fit</button>
      </div>
    </div>
  );
});

// A small overview in the corner: every step as a dot, the visible area as a frame. Click it to jump there.
function Minimap({ nodes, edges, view, size, onJump }) {
  if (nodes.length < 2) return null;
  const W = 170, H = 110, pad = 30;
  const minX = Math.min(...nodes.map((n) => n.x)) - pad, minY = Math.min(...nodes.map((n) => n.y)) - pad;
  const maxX = Math.max(...nodes.map((n) => n.x + nodeWidth(n, edges))) + pad, maxY = Math.max(...nodes.map((n) => n.y + NODE_H)) + pad;
  const vx = -view.x / view.k, vy = -view.y / view.k, vw = size.width / view.k, vh = size.height / view.k;
  const x0 = Math.min(minX, vx), y0 = Math.min(minY, vy), x1 = Math.max(maxX, vx + vw), y1 = Math.max(maxY, vy + vh);
  const k = Math.min(W / (x1 - x0), H / (y1 - y0));
  const pick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    onJump(x0 + (e.clientX - r.left) / k, y0 + (e.clientY - r.top) / k);
  };
  return (
    <svg className="fb-minimap" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Overview of the whole flow. Click to move there." onPointerDown={pick}>
      {nodes.map((n) => <rect key={n.id} x={(n.x - x0) * k} y={(n.y - y0) * k} width={Math.max(4, nodeWidth(n, edges) * k)} height={Math.max(3, NODE_H * k)} rx="1.5"
        className={`fb-mini-node${n.type === "note" ? " note" : ""}`} />)}
      <rect x={(vx - x0) * k} y={(vy - y0) * k} width={vw * k} height={vh * k} className="fb-mini-view" />
    </svg>
  );
}

export default FlowCanvas;
