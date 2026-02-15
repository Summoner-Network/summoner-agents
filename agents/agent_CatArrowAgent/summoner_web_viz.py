from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

ParseRouteFn = Callable[[str], Any]


def _tok(x: Any) -> str:
    return str(x).strip()


def dna_to_graph(dna: List[Dict[str, Any]], parse_route: Optional[ParseRouteFn] = None) -> Dict[str, Any]:
    """
    Base extraction:

      - Nodes are tokens that appear as source/target of some parsed route.
      - Each arrow is a directed edge source -> target with labels=[...].
      - Labels are NOT nodes (unless they also appear as a source/target elsewhere).

    Rendering builds independent truncation layers (k-1,k).
    """
    nodes: set[str] = set()
    edges: List[Dict[str, Any]] = []
    seen_edges: set[Tuple[str, str, str]] = set()  # (src,tgt,labels_joined)

    for item in dna:
        route = str(item.get("route", "")).strip()
        typ = str(item.get("type", ""))

        if not route:
            continue

        if parse_route is not None:
            pr = parse_route(route)

            src = [_tok(n) for n in getattr(pr, "source", ()) or ()]
            lab = [_tok(n) for n in getattr(pr, "label", ()) or ()]
            tgt = [_tok(n) for n in getattr(pr, "target", ()) or ()]

            is_arrow = bool(tgt) or bool(lab)

            if is_arrow:
                for s in (src or ["∅"]):
                    nodes.add(s)
                for t in (tgt or ["∅"]):
                    nodes.add(t)

                labels_joined = "|".join(lab)

                for s in (src or ["∅"]):
                    for t in (tgt or ["∅"]):
                        key = (s, t, labels_joined)
                        if key in seen_edges:
                            continue
                        seen_edges.add(key)
                        edges.append({"source": s, "target": t, "labels": lab, "route": route})
            else:
                for s in src:
                    nodes.add(s)
            continue

        # fallback: if no parse_route, show receive routes as nodes
        if typ == "receive":
            nodes.add(route)

    return {"nodes": sorted(nodes), "edges": edges}


_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>__TITLE__</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    :root {
      --bg0: #fbfbfc;
      --bg1: #f2f4f7;
      --ink: #111827;
      --muted: #4b5563;
      --stroke2: rgba(17, 24, 39, 0.25);
      --panel: rgba(255,255,255,0.82);
      --panelStroke: rgba(17,24,39,0.12);
      --on: #22c55e;
      --off: #d1d5db;
      --shadow2: rgba(17, 24, 39, 0.08);
    }

    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
      color: var(--ink);
      background: linear-gradient(180deg, var(--bg0), var(--bg1));
      overflow: hidden;
    }

    #topbar {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 10px 14px;
      background: var(--panel);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--panelStroke);
      box-shadow: 0 6px 18px var(--shadow2);
      user-select: none;
    }
    #title {
      font-weight: 650;
      letter-spacing: 0.2px;
      white-space: nowrap;
    }
    #status {
      color: var(--muted);
      font-size: 13px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
    #legend {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
      border: 1px solid var(--stroke2);
      box-shadow: 0 2px 8px var(--shadow2);
    }
    .dot.on { background: var(--on); }
    .dot.off { background: var(--off); }

    canvas { display: block; cursor: grab; }
    canvas.dragging { cursor: grabbing; }
  </style>
</head>
<body>
  <div id="topbar">
    <div id="title">__TITLE__</div>
    <div id="status">Loading…</div>
    <div id="legend">
      <span class="dot on"></span><span>active</span>
      <span class="dot off"></span><span>inactive</span>
      <span style="margin-left:10px; opacity:0.85;">Wheel: zoom</span>
      <span style="opacity:0.85;">Drag: pan</span>
      <span style="opacity:0.85;">Ctrl+Wheel: zoom column</span>
      <span style="opacity:0.85;">Double-click: reset</span>
    </div>
  </div>
  <canvas id="c"></canvas>

<script>
const canvas = document.getElementById("c");
const statusEl = document.getElementById("status");
const ctx = canvas.getContext("2d");

let graph = null;
let occupied = new Set();

/*
  View transform for the whole visualization.
*/
let VIEW = { scale: 1.0, tx: 0.0, ty: 0.0 };

/*
  Column zoom (Ctrl+Wheel) per truncation layer k.
*/
let COL_ZOOM = {}; // k -> float

/*
  Inferred dimensions for tokens (including label tokens).
*/
let DIMS = {};       // token -> dim
let MAX_DIM = 0;     // max dim among tokens (including labels)

/*
  Truncation layers:
    for k = 1..MAX_DIM+1:
      nodes dim == k-1
      edges whose label-dimension == k
  The last column (k = MAX_DIM+1) shows top cells as nodes even if there are no (MAX_DIM+1)-cells.
*/
let KS = [];               // [1..MAX_DIM+1]
let LAYER = {};            // k -> { nodes:[...], edges:[...] }
let BASE_POS = {};         // k -> {node -> {x,y}} in [-1,1]
let LAYER_META = {};       // k -> { maxPN, maxLabLen }

let DPR = 1;

function resize() {
  const topbarH = document.getElementById("topbar").offsetHeight;
  const W = window.innerWidth;
  const H = window.innerHeight - topbarH;

  DPR = Math.max(1, Math.floor((window.devicePixelRatio || 1) * 100) / 100);
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  canvas.width = Math.floor(W * DPR);
  canvas.height = Math.floor(H * DPR);
}
window.addEventListener("resize", () => { resize(); render(); });
resize();

/* ---------------------------
   Utilities
---------------------------- */

function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
function hypot(dx, dy) { return Math.sqrt(dx*dx + dy*dy); }

function unit(dx, dy) {
  const len = hypot(dx, dy) || 1;
  return { x: dx / len, y: dy / len, len };
}

function quadPoint(p0, p1, p2, t) {
  const mt = 1 - t;
  const a = mt * mt;
  const b = 2 * mt * t;
  const c = t * t;
  return { x: a*p0.x + b*p1.x + c*p2.x, y: a*p0.y + b*p1.y + c*p2.y };
}

function quadTangent(p0, p1, p2, t) {
  const dx = 2*(1-t)*(p1.x - p0.x) + 2*t*(p2.x - p1.x);
  const dy = 2*(1-t)*(p1.y - p0.y) + 2*t*(p2.y - p1.y);
  return unit(dx, dy);
}

// "Top side" normal for canvas coordinates (y increases downward):
// clockwise perpendicular (ux,uy) -> (uy,-ux). For left->right, that points up.
function topNormal(ux, uy) {
  let nx = uy;
  let ny = -ux;
  const len = Math.sqrt(nx*nx + ny*ny) || 1;
  return { x: nx / len, y: ny / len };
}

function snap(x) {
  const eff = DPR * (VIEW.scale || 1);
  return Math.round(x * eff) / eff;
}
function snapPt(p) { return { x: snap(p.x), y: snap(p.y) }; }

function canvasRect() { return canvas.getBoundingClientRect(); }

function screenToWorld(sx, sy) {
  return {
    x: (sx - VIEW.tx) / VIEW.scale,
    y: (sy - VIEW.ty) / VIEW.scale
  };
}

/* ---------------------------
   Dimension inference (generic, any height)
---------------------------- */

function computeDims(nodes, edges) {
  const dim = {};
  for (const n of nodes) dim[n] = 0;

  // add label tokens into the dim map
  for (const e of edges) {
    const labs = e.labels || [];
    for (const l of labs) {
      const s = String(l);
      if (!(s in dim)) dim[s] = 0;
    }
  }

  let changed = true;
  for (let it = 0; it < 64 && changed; it++) {
    changed = false;
    for (const e of edges) {
      const s = String(e.source);
      const t = String(e.target);
      if (!(s in dim)) dim[s] = 0;
      if (!(t in dim)) dim[t] = 0;

      const base = Math.max(dim[s] || 0, dim[t] || 0);

      if ((dim[s] || 0) < base) { dim[s] = base; changed = true; }
      if ((dim[t] || 0) < base) { dim[t] = base; changed = true; }

      const labs = e.labels || [];
      for (const l0 of labs) {
        const l = String(l0);
        if (!(l in dim)) dim[l] = 0;
        if ((dim[l] || 0) < base + 1) { dim[l] = base + 1; changed = true; }
      }
    }
  }

  let maxD = 0;
  for (const k in dim) maxD = Math.max(maxD, dim[k] || 0);

  return { dim, maxD };
}

/* ---------------------------
   Build truncation layers (independent 1-graphs)
---------------------------- */

function edgeK(e) {
  const labs = e.labels || [];

  // Unlabelled arrow: treat it as a (base+1)-cell so it shows up.
  // For ordinary object-to-object arrows (0-cells), this puts it in k=1.
  const s = String(e.source);
  const t = String(e.target);
  const base = Math.max(DIMS[s] ?? 0, DIMS[t] ?? 0);
  if (labs.length === 0) return base + 1;

  // Labelled arrow: keep existing behavior (dimension driven by label tokens).
  let k = 0;
  for (const l0 of labs) {
    const l = String(l0);
    k = Math.max(k, DIMS[l] ?? 0);
  }
  return k;
}

function buildLayers(allNodes, allEdges) {
  const tokenSet = new Set(allNodes);
  for (const e of allEdges) {
    for (const l0 of (e.labels || [])) tokenSet.add(String(l0));
  }
  const tokens = Array.from(tokenSet);

  const byDim = new Map();
  for (const t of tokens) {
    const d = DIMS[t] ?? 0;
    if (!byDim.has(d)) byDim.set(d, []);
    byDim.get(d).push(t);
  }

  const maxK = MAX_DIM + 1;
  const ks = [];
  const layer = {};

  for (let k = 1; k <= maxK; k++) {
    ks.push(k);
    layer[k] = { nodes: [], edges: [] };
  }

  for (const e of allEdges) {
    const k = edgeK(e);
    if (k <= 0) continue;
    if (k > maxK) continue;
    layer[k].edges.push(e);
  }

  for (let k = 1; k <= maxK; k++) {
    const wantDim = k - 1;
    const hasEdges = (layer[k].edges.length > 0);

    const ns = new Set();

    if (hasEdges) {
      for (const e of layer[k].edges) {
        const s = String(e.source);
        const t = String(e.target);
        if ((DIMS[s] ?? 0) === wantDim) ns.add(s);
        if ((DIMS[t] ?? 0) === wantDim) ns.add(t);
      }
    } else {
      for (const t of (byDim.get(wantDim) || [])) ns.add(t);
    }

    layer[k].nodes = Array.from(ns);
  }

  return { ks, layer };
}

function computeLayerMeta() {
  const meta = {};
  for (const k of KS) {
    const edges = (LAYER[k] && LAYER[k].edges) ? LAYER[k].edges : [];
    const cnt = new Map();
    let maxPN = 1;
    let maxLabLen = 0;

    for (const e of edges) {
      const key = String(e.source) + "->" + String(e.target);
      cnt.set(key, (cnt.get(key) || 0) + 1);

      const labs = e.labels || [];
      for (const l0 of labs) {
        maxLabLen = Math.max(maxLabLen, String(l0).length);
      }
    }

    for (const v of cnt.values()) maxPN = Math.max(maxPN, v);

    meta[k] = { maxPN, maxLabLen };
  }
  return meta;
}

/* ---------------------------
   Local layout per layer
---------------------------- */

function frLayout(nodes, edges) {
  const n = nodes.length;
  const pos = {};
  if (n === 0) return pos;

  for (let i = 0; i < n; i++) {
    const th = (2*Math.PI*i)/n;
    pos[nodes[i]] = { x: Math.cos(th)*0.75, y: Math.sin(th)*0.75 };
  }

  const idx = new Map();
  for (let i = 0; i < n; i++) idx.set(nodes[i], i);

  const adj = [];
  const adjSeen = new Set();

  for (const e of edges) {
    const s = String(e.source), t = String(e.target);
    if (!idx.has(s) || !idx.has(t) || s === t) continue;

    // For layout, direction usually doesn't matter; use undirected key
    const a = s < t ? s : t;
    const b = s < t ? t : s;
    const key = a + "<->" + b;

    if (adjSeen.has(key)) continue;
    adjSeen.add(key);

    adj.push([s, t]);
  }

  const area = 4.0;
  const k = Math.sqrt(area / Math.max(1, n));
  let temp = 0.35;

  const pairCap = (n <= 220);
  const iters = Math.min(220, 80 + n * 2);

  for (let it = 0; it < iters; it++) {
    const disp = {};
    for (const v of nodes) disp[v] = { x: 0, y: 0 };

    if (pairCap) {
      for (let i = 0; i < n; i++) {
        const v = nodes[i];
        for (let j = i + 1; j < n; j++) {
          const u = nodes[j];
          const dx = pos[v].x - pos[u].x;
          const dy = pos[v].y - pos[u].y;
          const dist = Math.max(1e-4, Math.sqrt(dx*dx + dy*dy));
          const force = (k*k) / dist;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          disp[v].x += fx; disp[v].y += fy;
          disp[u].x -= fx; disp[u].y -= fy;
        }
      }
    } else {
      const sample = Math.min(180, n);
      for (let i = 0; i < n; i++) {
        const v = nodes[i];
        for (let s = 0; s < sample; s++) {
          const j = (i*17 + s*29) % n;
          if (j === i) continue;
          const u = nodes[j];
          const dx = pos[v].x - pos[u].x;
          const dy = pos[v].y - pos[u].y;
          const dist = Math.max(1e-4, Math.sqrt(dx*dx + dy*dy));
          const force = (k*k) / dist;
          disp[v].x += (dx / dist) * force;
          disp[v].y += (dy / dist) * force;
        }
      }
    }

    for (const [s, t] of adj) {
      const dx = pos[s].x - pos[t].x;
      const dy = pos[s].y - pos[t].y;
      const dist = Math.max(1e-4, Math.sqrt(dx*dx + dy*dy));
      const force = (dist*dist) / k;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      disp[s].x -= fx; disp[s].y -= fy;
      disp[t].x += fx; disp[t].y += fy;
    }

    for (const v of nodes) {
      const dx = disp[v].x;
      const dy = disp[v].y;
      const d = Math.max(1e-4, Math.sqrt(dx*dx + dy*dy));
      const step = Math.min(d, temp);
      pos[v].x += (dx / d) * step;
      pos[v].y += (dy / d) * step;
      pos[v].x = clamp(pos[v].x, -0.98, 0.98);
      pos[v].y = clamp(pos[v].y, -0.98, 0.98);
    }

    temp *= 0.985;
    if (temp < 0.02) break;
  }

  return pos;
}

function buildBasePos() {
  const base = {};
  for (const k of KS) {
    base[k] = frLayout(LAYER[k].nodes, LAYER[k].edges);
  }
  return base;
}

/* ---------------------------
   Column geometry (zoomable columns)
---------------------------- */

function getColZoom(k) {
  const z = COL_ZOOM[k];
  if (typeof z === "number" && isFinite(z) && z > 0) return z;
  return 1.0;
}

function buildColRects(ks, W) {
  const leftMargin = W * 0.05;
  const rightMargin = W * 0.05;
  const gap = Math.min(22, Math.max(10, W * 0.01));

  const weights = [];
  let sumW = 0;
  for (const k of ks) {
    const w = getColZoom(k);
    weights.push(w);
    sumW += w;
  }

  const span = Math.max(1, (W - leftMargin - rightMargin - gap * (ks.length - 1)));

  const rects = new Map();
  let x = leftMargin;
  for (let i = 0; i < ks.length; i++) {
    const k = ks[i];
    const w = weights[i] / Math.max(1e-6, sumW);
    const cw = Math.max(160, span * w);
    const cx = x + cw / 2;
    rects.set(k, { left: x, right: x + cw, cx, width: cw });
    x += cw + gap;
  }
  return rects;
}

/* ---------------------------
   Drawing primitives
---------------------------- */

function drawBackground() {
  const W = canvas.width / DPR;
  const H = canvas.height / DPR;

  ctx.clearRect(0, 0, W, H);

  const g = ctx.createRadialGradient(W*0.5, H*0.45, 40, W*0.5, H*0.45, Math.max(W, H)*0.75);
  g.addColorStop(0, "rgba(255,255,255,0.90)");
  g.addColorStop(1, "rgba(243,245,248,0.95)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  const v = ctx.createRadialGradient(W*0.5, H*0.5, Math.min(W,H)*0.15, W*0.5, H*0.5, Math.max(W,H)*0.65);
  v.addColorStop(0, "rgba(255,255,255,0)");
  v.addColorStop(1, "rgba(17,24,39,0.035)");
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, W, H);
}

function cellLabel(n) {
  return `${n}-cells`;
}

function drawColumns(ks, rects, topPad, bottomPad) {
  const W = canvas.width / DPR;
  const H = canvas.height / DPR;
  const bandTop = topPad;
  const bandH = Math.max(1, H - topPad - bottomPad);

  for (const k of ks) {
    const r = rects.get(k);
    if (!r) continue;

    ctx.save();
    ctx.beginPath();
    ctx.rect(r.left, bandTop, r.width, bandH);
    ctx.fillStyle = "rgba(17,24,39,0.018)";
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(r.left, bandTop);
    ctx.lineTo(r.left, bandTop + bandH);
    ctx.moveTo(r.right, bandTop);
    ctx.lineTo(r.right, bandTop + bandH);
    ctx.strokeStyle = "rgba(17,24,39,0.045)";
    ctx.lineWidth = 1.0 / VIEW.scale;
    ctx.stroke();
    ctx.restore();

    const s = VIEW.scale;
    ctx.save();
    ctx.fillStyle = "rgba(75,85,99,0.88)";
    ctx.font = `${12 / s}px ui-sans-serif, system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    let text = "";
    if (k <= MAX_DIM) {
      text = `Graph of ${cellLabel(k - 1)} and ${cellLabel(k)}`;
    } else {
      text = `Set of ${cellLabel(MAX_DIM)}`;
    }

    ctx.fillText(text, r.cx, (bandTop - 18 / s));
    ctx.restore();
  }
}

function drawArrowHead(x, y, angle, size) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);

  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(-size, -size * 0.58);
  ctx.lineTo(-size,  size * 0.58);
  ctx.closePath();
  ctx.fill();

  ctx.restore();
}

/* ---------------------------
   Bubble metrics + collision avoidance
---------------------------- */

function bubbleMetrics(text) {
  const s = VIEW.scale;
  const fontPx = 12.5 / s;
  ctx.font = `${fontPx}px ui-sans-serif, system-ui`;
  const w = ctx.measureText(text).width;
  const r = Math.max(15.5 / s, (w / 2) + (10 / s));
  return { fontPx, r };
}

function estimateLabelRadiusFromLen(nChars) {
  const s = VIEW.scale;
  const fontPx = 12.5 / s;
  const charPx = 0.58 * fontPx; // rough sans-serif average width
  const w = Math.max(0, nChars) * charPx;
  return Math.max(15.5 / s, (w / 2) + (10 / s));
}

function drawBubble(text, x, y, isOn, metrics /* optional */) {
  const s = VIEW.scale;
  const m = metrics || bubbleMetrics(text);
  const r = m.r;

  ctx.save();
  ctx.shadowColor = "rgba(17,24,39,0.16)";
  ctx.shadowBlur = 14 / s;
  ctx.shadowOffsetY = 6 / s;

  ctx.beginPath();
  ctx.arc(x, y, r, 0, 2*Math.PI);
  ctx.fillStyle = isOn ? "rgba(34,197,94,0.92)" : "rgba(209,213,219,0.92)";
  ctx.fill();
  ctx.restore();

  ctx.beginPath();
  ctx.arc(x, y, r, 0, 2*Math.PI);
  ctx.strokeStyle = "rgba(17,24,39,0.35)";
  ctx.lineWidth = 1.25 / s;
  ctx.stroke();

  ctx.fillStyle = "rgba(17,24,39,0.92)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `${m.fontPx}px ui-sans-serif, system-ui`;
  ctx.fillText(text, x, y);
}

function circlesOverlap(x1, y1, r1, x2, y2, r2, pad) {
  const dx = x1 - x2, dy = y1 - y2;
  const rr = r1 + r2 + pad;
  return (dx*dx + dy*dy) < (rr*rr);
}

function placeCircleGreedy(x0, y0, r, nx, ny, tx, ty, obstacles, bounds) {
  const s = VIEW.scale;
  const pad  = 3 / s;
  const step = 12 / s;

  function insideBounds(x, y) {
    if (!bounds) return true;
    return (
      x - r >= bounds.x0 &&
      x + r <= bounds.x1 &&
      y - r >= bounds.y0 &&
      y + r <= bounds.y1
    );
  }

  function ok(x, y) {
    if (!insideBounds(x, y)) return false;
    for (const o of obstacles) {
      if (circlesOverlap(x, y, r, o.x, o.y, o.r, pad)) return false;
    }
    return true;
  }

  // 1) stack along normal: 0, +1, -1, +2, -2, ...
  for (let j = 0; j <= 10; j++) {
    if (j === 0) {
      if (ok(x0, y0)) return { x: x0, y: y0 };
      continue;
    }
    const dn = j * step;

    const xA = x0 + nx * dn, yA = y0 + ny * dn;
    if (ok(xA, yA)) return { x: xA, y: yA };

    const xB = x0 - nx * dn, yB = y0 - ny * dn;
    if (ok(xB, yB)) return { x: xB, y: yB };
  }

  // 2) if still colliding, add tangent shifts too
  for (let j = 1; j <= 6; j++) {
    const dt = j * step * 1.15;

    const candidates = [
      { dn: 0,    dt:  dt },
      { dn: 0,    dt: -dt },
      { dn: step, dt:  dt },
      { dn:-step, dt: -dt },
      { dn: step, dt: -dt },
      { dn:-step, dt:  dt },
    ];

    for (const c of candidates) {
      const x = x0 + nx * c.dn + tx * c.dt;
      const y = y0 + ny * c.dn + ty * c.dt;
      if (ok(x, y)) return { x, y };
    }
  }

  // fallback: clamp into bounds if available
  if (bounds) {
    return {
      x: clamp(x0, bounds.x0 + r, bounds.x1 - r),
      y: clamp(y0, bounds.y0 + r, bounds.y1 - r),
    };
  }
  return { x: x0, y: y0 };
}

/* ---------------------------
   Node drawing
---------------------------- */

function drawNode(label, x, y, isOn) {
  const s = VIEW.scale;
  const R = 26 / s;

  ctx.save();
  ctx.shadowColor = "rgba(17,24,39,0.16)";
  ctx.shadowBlur = 18 / s;
  ctx.shadowOffsetY = 8 / s;

  ctx.beginPath();
  ctx.arc(x, y, R, 0, 2*Math.PI);
  ctx.fillStyle = isOn ? "rgba(34,197,94,0.92)" : "rgba(209,213,219,0.92)";
  ctx.fill();
  ctx.restore();

  ctx.beginPath();
  ctx.arc(x, y, R, 0, 2*Math.PI);
  ctx.strokeStyle = "rgba(17,24,39,0.42)";
  ctx.lineWidth = 1.35 / s;
  ctx.stroke();

  ctx.fillStyle = "rgba(17,24,39,0.92)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `${13 / s}px ui-sans-serif, system-ui`;
  ctx.fillText(label, x, y);
}

/* ---------------------------
   Layer layout -> absolute positions (per column)
   Includes:
     - conditional left-right flip (so arrows tend to point left-to-right)
     - auto-fit to keep each layer within its column frame
---------------------------- */

function layoutAll() {
  const W = canvas.width / DPR;
  const H = canvas.height / DPR;

  const topPad = 44;
  const bottomPad = 22;
  const rects = buildColRects(KS, W);

  const bandTop = topPad;
  const bandH = Math.max(1, H - topPad - bottomPad);
  const cy = bandTop + bandH / 2;

  const pos = {};
  for (const k of KS) {
    pos[k] = {};
    const r = rects.get(k);
    if (!r) continue;

    const base = BASE_POS[k] || {};
    const z = getColZoom(k);

    // initial spread: column zoom makes internal layout larger
    let spreadX = (r.width * 0.42) * Math.sqrt(z);
    let spreadY = (bandH * 0.42) * Math.sqrt(z);

    for (const n of (LAYER[k].nodes || [])) {
      const p0 = base[n] || { x: 0, y: 0 };
      pos[k][n] = { x: r.cx + p0.x * spreadX, y: cy + p0.y * spreadY };
    }

    // If edges mostly point right-to-left, flip horizontally within this column.
    const edges = LAYER[k].edges || [];
    let sumDx = 0;
    let cntDx = 0;
    for (const e of edges) {
      const s = String(e.source), t = String(e.target);
      const ps = pos[k][s];
      const pt = pos[k][t];
      if (!ps || !pt) continue;
      sumDx += (pt.x - ps.x);
      cntDx += 1;
    }
    if (cntDx > 0) {
      const avgDx = sumDx / cntDx;
      if (avgDx < 0) {
        for (const n of (LAYER[k].nodes || [])) {
          const p = pos[k][n];
          if (!p) continue;
          p.x = r.cx - (p.x - r.cx);
        }
      }
    }

    // Auto-fit within the layer frame.
    // We fit node centers plus a conservative margin that accounts for:
    //   - node radius
    //   - curvature offsets
    //   - label rails and bubble radius (estimated)
    const nodes = LAYER[k].nodes || [];
    if (nodes.length > 0) {
      const s = VIEW.scale;
      const nodeR = 26 / s;

      const meta = LAYER_META[k] || { maxPN: 1, maxLabLen: 0 };
      const maxPN = Math.max(1, meta.maxPN || 1);
      const maxAbsEdgeJ = (maxPN - 1) / 2;

      const curveAbs = (18 / s) * Math.abs(maxAbsEdgeJ);
      const railAbs = (34 / s) + (12 / s) * Math.abs(maxAbsEdgeJ);
      const maxLabelR = estimateLabelRadiusFromLen(meta.maxLabLen || 0);

      const normalExtra = Math.max(curveAbs, railAbs) + maxLabelR + (10 / s);
      const padX = nodeR + normalExtra;
      const padY = nodeR + normalExtra;

      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of nodes) {
        const p = pos[k][n];
        if (!p) continue;
        minX = Math.min(minX, p.x);
        maxX = Math.max(maxX, p.x);
        minY = Math.min(minY, p.y);
        maxY = Math.max(maxY, p.y);
      }

      if (isFinite(minX) && isFinite(minY)) {
        const needW = (maxX - minX) + 2 * padX;
        const needH = (maxY - minY) + 2 * padY;

        const availW = r.width * 0.98;
        const availH = bandH * 0.98;

        const fW = availW / Math.max(1e-6, needW);
        const fH = availH / Math.max(1e-6, needH);
        const f = Math.min(1.0, fW, fH);

        if (f < 1.0) {
          for (const n of nodes) {
            const p = pos[k][n];
            if (!p) continue;
            p.x = r.cx + (p.x - r.cx) * f;
            p.y = cy + (p.y - cy) * f;
          }
        }
      }
    }
  }

  return { pos, rects, topPad, bottomPad };
}

/* ---------------------------
   Rendering
---------------------------- */

let LAST = null;

function render() {
  if (!graph) return;

  // background (screen space)
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  drawBackground();

  // graph layer (world space)
  ctx.setTransform(DPR * VIEW.scale, 0, 0, DPR * VIEW.scale, DPR * VIEW.tx, DPR * VIEW.ty);

  const layout = layoutAll();
  LAST = layout;

  drawColumns(KS, layout.rects, layout.topPad, layout.bottomPad);

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  const BASE_W = 2.0 / VIEW.scale;
  const BASE_A = 0.45;
  const W_REDUCE = 0.28 / VIEW.scale;
  const A_BOOST  = 0.13;

  const Wscreen = canvas.width / DPR;
  const Hscreen = canvas.height / DPR;
  const bandTop = layout.topPad;
  const bandH = Math.max(1, Hscreen - layout.topPad - layout.bottomPad);

  // Draw each layer clipped to its column frame
  for (const k of KS) {
    const nodes = LAYER[k].nodes || [];
    const edges = LAYER[k].edges || [];
    const pos = layout.pos[k] || {};
    const r = layout.rects.get(k);
    if (!r) continue;

    // Clip strictly to the column band so nothing bleeds across columns
    ctx.save();
    ctx.beginPath();
    ctx.rect(r.left, bandTop, r.width, bandH);
    ctx.clip();

    // stable order reduces jitter
    edges.sort((a, b) => {
      const ka = String(a.source) + "->" + String(a.target) + "|" + String(a.route || "");
      const kb = String(b.source) + "->" + String(b.target) + "|" + String(b.route || "");
      return ka.localeCompare(kb);
    });

    // group parallel edges
    const groups = new Map();
    for (const e of edges) {
      const key = String(e.source) + "->" + String(e.target);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(e);
    }
    for (const es of groups.values()) {
      for (let i = 0; i < es.length; i++) {
        es[i]._pi = i;
        es[i]._pn = es.length;
      }
    }

    // obstacles: nodes + placed labels
    const nodeR = 26 / VIEW.scale;
    const obstacles = [];
    for (const n of nodes) {
      const p = pos[n];
      if (p) obstacles.push({ x: p.x, y: p.y, r: nodeR });
    }

    // bounds for label circles inside this column band (conservative)
    const bounds = {
      x0: r.left + (6 / VIEW.scale),
      x1: r.right - (6 / VIEW.scale),
      y0: bandTop + (6 / VIEW.scale),
      y1: bandTop + bandH - (6 / VIEW.scale),
    };

    // edges + labels
    for (const e of edges) {
      const pS = pos[String(e.source)];
      const pT = pos[String(e.target)];
      if (!pS || !pT) continue;

      const dx = pT.x - pS.x;
      const dy = pT.y - pS.y;
      const u = unit(dx, dy);
      if (u.len < 1e-3) continue;

      const pad = 2 / VIEW.scale;

      let start = { x: pS.x + u.x * (nodeR + pad), y: pS.y + u.y * (nodeR + pad) };
      let end   = { x: pT.x - u.x * (nodeR + pad), y: pT.y - u.y * (nodeR + pad) };

      const nTop = topNormal(u.x, u.y);

      const pn = e._pn || 1;
      const pi = e._pi || 0;
      const edgeJ = (pi - (pn - 1) / 2);

      const spread = 18 / VIEW.scale;
      const curve = edgeJ * spread;

      const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
      let ctrl = { x: mid.x + nTop.x * curve, y: mid.y + nTop.y * curve };

      start = snapPt(start);
      end   = snapPt(end);
      ctrl  = snapPt(ctrl);

      const ang = Math.atan2(u.y, u.x);
      const diag = Math.abs(Math.sin(2 * ang));
      const lw = BASE_W - W_REDUCE * diag;
      const a  = BASE_A + A_BOOST  * diag;

      ctx.lineWidth = lw;
      ctx.strokeStyle = `rgba(17,24,39,${a.toFixed(3)})`;
      ctx.fillStyle = ctx.strokeStyle;

      ctx.beginPath();
      ctx.moveTo(start.x, start.y);
      ctx.quadraticCurveTo(ctrl.x, ctrl.y, end.x, end.y);
      ctx.stroke();

      const tan = quadTangent(start, ctrl, end, 0.985);
      const headAng = Math.atan2(tan.y, tan.x);
      drawArrowHead(end.x, end.y, headAng, 12 / VIEW.scale);

      // label rail: per-edge rails + collision avoidance
      const railBase = 34 / VIEW.scale;
      const railStep = 12 / VIEW.scale;
      const railOff = railBase + edgeJ * railStep;

      const startR = snapPt({ x: start.x + nTop.x * railOff, y: start.y + nTop.y * railOff });
      const endR   = snapPt({ x: end.x   + nTop.x * railOff, y: end.y   + nTop.y * railOff });
      const ctrlR  = snapPt({ x: ctrl.x  + nTop.x * railOff, y: ctrl.y  + nTop.y * railOff });

      const labels = e.labels || [];
      if (labels.length > 0) {
        const m = labels.length;
        const alongGap = 0.09;

        const tCenter = clamp(0.5 + edgeJ * 0.06, 0.30, 0.70);
        const maxSpan = Math.min(0.26, alongGap * (m - 1));
        const t0 = tCenter - maxSpan / 2;

        for (let i = 0; i < m; i++) {
          const t = Math.max(0.12, Math.min(0.88, t0 + (m === 1 ? 0 : (maxSpan * i / (m - 1)))));
          const P0 = quadPoint(startR, ctrlR, endR, t);

          const tanL = quadTangent(startR, ctrlR, endR, t);
          const nL = topNormal(tanL.x, tanL.y);

          const lab = String(labels[i]);
          const met = bubbleMetrics(lab);

          const placed = placeCircleGreedy(P0.x, P0.y, met.r, nL.x, nL.y, tanL.x, tanL.y, obstacles, bounds);
          const P = snapPt(placed);

          drawBubble(lab, P.x, P.y, occupied.has(lab), met);
          obstacles.push({ x: P.x, y: P.y, r: met.r });
        }
      }
    }

    // nodes
    for (const n of nodes) {
      const p = pos[n];
      if (!p) continue;
      drawNode(n, p.x, p.y, occupied.has(n));
    }

    ctx.restore();
  }
}

/* ---------------------------
   Boot: load graph + poll state
---------------------------- */

async function loadGraph() {
  try {
    statusEl.textContent = "Loading graph…";
    const r = await fetch("/graph");
    if (!r.ok) throw new Error("GET /graph failed: " + r.status);
    graph = await r.json();

    const nodes = graph.nodes || [];
    const edges = graph.edges || [];

    const d = computeDims(nodes, edges);
    DIMS = d.dim;
    MAX_DIM = d.maxD;

    const built = buildLayers(nodes, edges);
    KS = built.ks;
    LAYER = built.layer;

    for (const k of KS) {
      if (!(k in COL_ZOOM)) COL_ZOOM[k] = 1.0;
    }

    BASE_POS = buildBasePos();
    LAYER_META = computeLayerMeta();

    statusEl.textContent = "Graph loaded. Waiting for state…";
    render();
  } catch (e) {
    statusEl.textContent = "Load error: " + String(e);
    console.error(e);
  }
}

async function pollState() {
  try {
    const r = await fetch("/state");
    if (!r.ok) throw new Error("GET /state failed: " + r.status);
    const s = await r.json();
    const items = s.states || [];
    occupied = new Set(items);
    statusEl.textContent = "states = [" + items.join(", ") + "]";
    render();
  } catch (e) {
    statusEl.textContent = "Disconnected";
  }
}

/* ---------------------------
   Interaction: pan/zoom + column zoom
---------------------------- */

let dragging = false;
let last = { x: 0, y: 0 };

canvas.addEventListener("mousedown", (e) => {
  dragging = true;
  canvas.classList.add("dragging");
  last = { x: e.clientX, y: e.clientY };
});

window.addEventListener("mouseup", () => {
  dragging = false;
  canvas.classList.remove("dragging");
});

window.addEventListener("mousemove", (e) => {
  if (!dragging) return;
  const dx = e.clientX - last.x;
  const dy = e.clientY - last.y;
  last = { x: e.clientX, y: e.clientY };

  VIEW.tx += dx;
  VIEW.ty += dy;
  render();
});

canvas.addEventListener("dblclick", () => {
  VIEW = { scale: 1.0, tx: 0.0, ty: 0.0 };
  for (const k of KS) COL_ZOOM[k] = 1.0;
  render();
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (!graph || !LAST) return;

  const rect = canvasRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  const delta = -e.deltaY;
  const zoomFactor = Math.exp(delta * 0.0012);

  const w0 = screenToWorld(sx, sy);

  if (e.ctrlKey) {
    const rects = LAST.rects;
    let hit = null;
    for (const k of KS) {
      const r = rects.get(k);
      if (!r) continue;
      if (w0.x >= r.left && w0.x <= r.right) { hit = k; break; }
    }
    if (hit != null) {
      const z0 = getColZoom(hit);
      const z1 = clamp(z0 * zoomFactor, 0.35, 6.0);
      COL_ZOOM[hit] = z1;
      render();
    }
    return;
  }

  const s0 = VIEW.scale;
  const s1 = clamp(s0 * zoomFactor, 0.18, 6.0);

  VIEW.scale = s1;
  VIEW.tx = sx - w0.x * s1;
  VIEW.ty = sy - w0.y * s1;

  render();
}, { passive: false });

/* ---------------------------
   Boot
---------------------------- */

loadGraph().then(() => {
  setInterval(pollState, 200);
  pollState();
});
</script>
</body>
</html>
"""


class _VizState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.graph: Dict[str, Any] = {"nodes": [], "edges": []}
        self.states: List[str] = []


class WebGraphVisualizer:
    def __init__(self, *, title: str = "Summoner Graph", port: int = 8765) -> None:
        self.title = title
        self.port = port
        self._st = _VizState()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def set_graph_from_dna(self, dna: List[Dict[str, Any]], *, parse_route: Optional[ParseRouteFn] = None) -> None:
        g = dna_to_graph(dna, parse_route=parse_route)
        with self._st.lock:
            self._st.graph = g

    def push_states(self, states: Any) -> None:
        labels: List[str] = []

        def add(x: Any) -> None:
            labels.append(_tok(x))

        if isinstance(states, dict):
            for _k, vs in states.items():
                if isinstance(vs, (list, tuple)):
                    for v in vs:
                        add(v)
                else:
                    add(vs)
        elif isinstance(states, (list, tuple)):
            for s in states:
                add(s)
        else:
            add(states)

        with self._st.lock:
            self._st.states = labels

    def start(self, *, open_browser: bool = True) -> None:
        if self._thread is not None:
            return

        st = self._st
        title = self.title

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/" or self.path.startswith("/?"):
                    html = _HTML.replace("__TITLE__", title)
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return

                if self.path == "/graph":
                    with st.lock:
                        body = json.dumps(st.graph).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                    return

                if self.path == "/state":
                    with st.lock:
                        body = json.dumps({"states": st.states}).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                    return

                self._send(404, b"Not found", "text/plain; charset=utf-8")

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)

        def run() -> None:
            assert self._server is not None
            self._server.serve_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

        if open_browser:
            time.sleep(0.15)
            webbrowser.open(f"http://127.0.0.1:{self.port}/")
