import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from "react";
import { GRID, NODE_H, NODE_W, OUTPUT_LABELS, groupOf, iconOf, iconPath, isTrigger, outputsOf, summaryOf, titleOf } from "./catalog.js";
import { curve, inPort, nodeWidth, outPort, snap } from "./graph.js";

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2;
const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
const clamp = (v) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v));

// The drawing surface: drag the background to pan, wheel or buttons to zoom, drag a card to move it, drag from an
// output port to another card to connect them. Cards and connections are focusable buttons for keyboard users.
const FlowCanvas = forwardRef(function FlowCanvas(props, ref) {
  const { nodes, edges, selected, problems, highlight, caller, onSelect, onDragStart, onPreviewMove, onCommitMove,
    onKeyMove, onConnect, onDeleteEdge, onDeleteNode, onOpenConfig, onDropType } = props;
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
    }
    setDrag(null);
    setPointer(null);
  };

  const keyNode = (e, node) => {
    const step = e.shiftKey ? GRID * 5 : GRID;
    const moves = { ArrowUp: [0, -step], ArrowDown: [0, step], ArrowLeft: [-step, 0], ArrowRight: [step, 0] };
    if (moves[e.key]) { e.preventDefault(); onKeyMove(node.id, ...moves[e.key]); }
    else if (e.key === "Enter") { e.preventDefault(); onOpenConfig(node.id); }
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
            const label = OUTPUT_LABELS[e.label] ?? e.label;
            return (
              <g key={e.id} className={`fb-edge${highlight.has(e.id) ? " on" : ""}${isSel ? " sel" : ""}${e.label === "error" ? " err" : ""}`}
                data-edge-id={e.id} tabIndex={0} role="button" aria-pressed={isSel}
                aria-label={`Connection from ${titleOf(a)}${label ? ` (${label})` : ""} to ${titleOf(b)}. Press Delete to remove.`}
                onPointerDown={(ev) => { ev.stopPropagation(); onSelect({ kind: "edge", id: e.id }); }}
                onFocus={() => onSelect({ kind: "edge", id: e.id })} onKeyDown={(ev) => keyEdge(ev, e)}>
                <path d={d} className="fb-edge-hit" />
                <path d={d} className="fb-edge-line" markerEnd="url(#fb-arrow)" />
                {label && <text x={mid.x} y={mid.y - 6} className="fb-edge-label">{label}</text>}
                {isSel && (
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
            const width = nodeWidth(n, edges);
            const outs = outputsOf(n, edges);
            const issues = problems[n.id] || [];
            const isSel = selected?.kind === "node" && selected.id === n.id;
            const summary = summaryOf(n, caller);
            const cls = `fb-node g-${groupOf(n.type)}${isSel ? " sel" : ""}${issues.length ? " bad" : ""}${highlight.has(n.id) ? " on" : ""}`;
            return (
              <g key={n.id} data-node-id={n.id} className={cls} transform={`translate(${n.x},${n.y})`} tabIndex={0} role="button"
                aria-pressed={isSel} aria-label={`${titleOf(n)}: ${summary}${issues.length ? `. Problem: ${issues.join(" ")}` : ""}. Arrow keys move it, Enter edits, Delete removes.`}
                onPointerDown={(e) => startNode(e, n)} onFocus={() => onSelect({ kind: "node", id: n.id })}
                onKeyDown={(e) => keyNode(e, n)} onDoubleClick={() => onOpenConfig(n.id)}>
                <title>{[titleOf(n), summary, ...issues].join(" — ")}</title>
                <rect className="fb-card" width={width} height={NODE_H} rx="12" />
                <rect className="fb-band" width="6" height={NODE_H} rx="3" />
                <g transform="translate(16,11) scale(.75)"><path d={iconPath(iconOf(n.type))} className="fb-icon" /></g>
                <text x="44" y="27" className="fb-title">{clip(titleOf(n), Math.floor(width / 9))}</text>
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
                      {OUTPUT_LABELS[label] && <text className="fb-port-label" x={px} y={NODE_H + 22}>{OUTPUT_LABELS[label]}</text>}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </g>
      </svg>
      <div className="fb-zoom" role="toolbar" aria-label="Zoom">
        <button type="button" className="btn ghost" aria-label="Zoom out" onClick={() => { const r = rect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2); }}>−</button>
        <span aria-live="polite">{Math.round(view.k * 100)}%</span>
        <button type="button" className="btn ghost" aria-label="Zoom in" onClick={() => { const r = rect(); zoomAt(1.2, r.width / 2, r.height / 2); }}>+</button>
        <button type="button" className="btn ghost" onClick={fit}>Fit</button>
      </div>
    </div>
  );
});

export default FlowCanvas;
