// Cytoscape scene for topology, plus a DOM overlay for packet markers and node badges.
// Shapes carry the meaning (bus bar vs star hub, router square vs host round box); colour only adds emphasis.

/* How long a marker takes to cross a line: the channel delay, at least 1 s of sim time so it stays easy to follow. */
export const travelMs = (delayMs, speed = 1) => Math.max(150, Math.max(delayMs, 1000) / speed);

let cy = null;
let overlay = null;
let topo = null;
let pos = {};
let lanPos = {};
let markers = [];
let raf = 0;
let onSel = () => {};

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function initGraph(container, onSelect) {
  onSel = onSelect;
  overlay = document.getElementById('overlay');
  const ink = cssVar('--ink'), line2 = cssVar('--edge'), accent = cssVar('--accent'), surface = cssVar('--surface');
  cy = cytoscape({
    container,
    minZoom: 0.4,
    maxZoom: 2.5,
    wheelSensitivity: 0.2,
    style: [
      { selector: 'node.host', style: { shape: 'round-rectangle', width: 110, height: 56, 'background-color': surface, 'border-color': line2, 'border-width': 1, label: 'data(label)', 'text-wrap': 'wrap', 'font-size': 12, 'font-family': 'IBM Plex Mono, monospace', color: ink, 'text-valign': 'center' } },
      { selector: 'node.router', style: { shape: 'rectangle', width: 110, height: 56, 'background-color': surface, 'border-color': ink, 'border-width': 3, label: 'data(label)', 'text-wrap': 'wrap', 'font-size': 12, 'font-family': 'IBM Plex Mono, monospace', color: ink, 'text-valign': 'center' } },
      { selector: 'node.bus', style: { shape: 'rectangle', width: 400, height: 12, 'background-color': ink, 'border-width': 0, label: 'data(label)', 'font-size': 12, color: ink, 'text-valign': 'bottom', 'text-margin-y': 8 } },
      { selector: 'node.star', style: { shape: 'ellipse', width: 56, height: 56, 'background-color': surface, 'border-color': ink, 'border-width': 2, label: 'data(label)', 'font-size': 12, color: ink, 'text-valign': 'bottom', 'text-margin-y': 6, 'text-wrap': 'wrap' } },
      { selector: 'node:selected', style: { 'overlay-opacity': 0, 'border-color': accent, 'border-width': 3 } },
      { selector: 'node.pulse', style: { 'border-width': 4, 'border-color': accent } },
      { selector: 'edge', style: { width: 2, 'line-color': line2, 'curve-style': 'straight', 'target-arrow-shape': 'none' } },
      { selector: 'edge.lit', style: { width: 5, 'line-color': ink } },
      { selector: 'edge:selected', style: { width: 4, 'line-color': accent } }
    ]
  });
  cy.on('tap', 'node', (e) => {
    const d = e.target.data();
    onSel(d.kind === 'lan' ? { kind: 'lan', id: +d.id.slice(3) } : { kind: 'node', id: d.id });
  });
  cy.on('tap', 'edge', (e) => onSel({ kind: 'edge', id: e.target.id() }));
  window.__cy = cy; // read by bridge/tests/test_frontend.py to find node positions
  cy.on('pan zoom', renderOverlay);
  new ResizeObserver(() => { cy.resize(); renderOverlay(); }).observe(container); // side panels change the canvas size
  cy.on('position', 'node', renderOverlay);
  return cy;
}

export function loadTopology(t) {
  topo = t;
  pos = {};
  lanPos = {};
  cy.elements().remove();
  t.lans.forEach((l) => {
    lanPos[l.id] = { x: l.x, y: l.y };
    const cls = l.mode === 'bus' ? 'bus' : 'star';
    cy.add({ group: 'nodes', data: { id: 'lan' + l.id, kind: 'lan', label: l.mode === 'bus' ? l.name : l.name + ' · STAR' }, classes: cls, position: { x: l.x, y: l.y } });
  });
  t.nodes.forEach((n) => {
    pos[n.id] = { x: n.x, y: n.y };
    const ip = n.ifaces[0] ? n.ifaces[0].ip : '';
    cy.add({ group: 'nodes', data: { id: n.id, kind: 'node', label: n.id + '\n' + ip }, classes: n.kind, position: { x: n.x, y: n.y } });
    n.ifaces.forEach((f) => {
      const lanNode = 'lan' + f.lan;
      cy.add({ group: 'edges', data: { id: n.id + ':' + f.idx, source: n.id, target: lanNode } });
    });
  });
  cy.fit(undefined, 40);
  renderOverlay();
}

// Lit edges (sender and receivers in the current window) get the "lit" class.
export function setLit(edgeIds) {
  cy.edges().removeClass('lit');
  edgeIds.forEach((id) => cy.getElementById(id).addClass('lit'));
}

export function pulseNode(id) {
  const n = cy.getElementById(id);
  n.addClass('pulse');
  setTimeout(() => n.removeClass('pulse'), 400);
}

export function selectEdgeOrNode(sel) {
  cy.elements().unselect();
  if (sel && sel.kind === 'node') cy.getElementById(sel.id).select();
  if (sel && sel.kind === 'edge') cy.getElementById(sel.id).select();
  if (sel && sel.kind === 'lan') cy.getElementById('lan' + sel.id).select();
}

/* ---- packet markers ---- */
// The marker rides the drawn lines: sender -> LAN centre -> receiver, read live so dragged nodes still match.
function ptsOf(m) {
  return m.ids.map((id) => cy.getElementById(id)).filter((e) => e.nonempty()).map((e) => e.position());
}

function pointAt(pts, f) {
  const segs = [];
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    const len = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    segs.push(len);
    total += len;
  }
  let d = total * Math.min(1, Math.max(0, f));
  for (let i = 0; i < segs.length; i++) {
    if (d <= segs[i] || i === segs.length - 1) {
      const r = segs[i] ? Math.min(1, d / segs[i]) : 0;
      return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * r, y: pts[i].y + (pts[i + 1].y - pts[i].y) * r };
    }
    d -= segs[i];
  }
  return pts[0];
}

// Converts model coordinates to container pixels using the current pan and zoom.
function toScreen(p) {
  const z = cy.zoom(), pan = cy.pan();
  return { x: p.x * z + pan.x, y: p.y * z + pan.y };
}

/* Spawn one marker. opts: { lan, from, to, kind, fate, ignored, delayMs, progress (static) }.
   Animated markers move in real time over delayMs/speed; static ones sit at opts.progress (replay). */
export function spawnMarker(opts) {
  const el = document.createElement('span');
  const cls = [ 'mk', opts.kind === 'req' ? 'req' : opts.kind === 'rep' ? 'rep' : opts.kind === 'proxy' ? 'proxy' : 'ip' ];
  if (opts.ignored) cls.push('ign');
  if (opts.fate === 'lost') cls.push('lost');
  if (opts.fate === 'corrupted') cls.push('cor');
  el.className = cls.join(' ');
  el.setAttribute('aria-hidden', 'true');
  if (opts.kind === 'proxy' && !opts.ignored) el.textContent = 'P';
  overlay.appendChild(el);
  const m = { el, ids: [opts.from, 'lan' + opts.lan, opts.to], fate: opts.fate, ignored: !!opts.ignored, to: opts.to, start: performance.now(), dur: travelMs(opts.delayMs, opts.speed || 1), progress: opts.progress, animated: opts.progress === undefined, fcsShown: false };
  markers.push(m);
  if (m.animated) startMarkers(); else paint(m, opts.progress);
  return m;
}

// Where along the path a marker sits, given its fate. Lost frames stop at mid-line.
function paint(m, p) {
  let f = p;
  if (m.fate === 'lost') f = Math.min(p, 0.5);
  const pts = ptsOf(m);
  if (pts.length < 2) return;
  const s = toScreen(pointAt(pts, f));
  m.el.style.left = s.x + 'px';
  m.el.style.top = s.y + 'px';
  let op = 1;
  if (m.fate === 'lost' && p > 0.5) { m.el.textContent = '✕'; op = Math.max(0, 1 - (p - 0.5) / 0.35); }
  if (m.ignored) op = Math.min(op, Math.max(0.25, 1 - p * 0.7));
  if (m.fate === 'corrupted' && p >= 1 && !m.fcsShown) { m.fcsShown = true; m.el.classList.add('fcs'); }
  m.el.style.opacity = op;
  if (m.fate === 'lost' && p >= 0.85) m.done = true;
}

function tick() {
  const now = performance.now();
  markers = markers.filter((m) => {
    if (!m.animated) return true;
    const p = (now - m.start) / m.dur;
    if (p >= 1.4 || m.done) { m.el.remove(); return false; }
    paint(m, Math.min(1, p));
    return true;
  });
  raf = markers.some((m) => m.animated) ? requestAnimationFrame(tick) : 0;
}

export function startMarkers() {
  if (!raf && markers.some((m) => m.animated)) raf = requestAnimationFrame(tick);
}

function renderOverlay() {
  markers.forEach((m) => { if (!m.animated) paint(m, m.progress); });
  overlay.querySelectorAll('[data-badge]').forEach((b) => b.remove());
  Object.keys(badges).forEach((id) => {
    const b = badges[id];
    const p = pos[id];
    if (!p) return;
    const s = toScreen({ x: p.x + 40, y: p.y - 26 });
    if (b.pending) addBadge(s, 'node-badge', '⏳ ' + b.pending, 'pending');
    if (b.err) addBadge({ x: s.x - 80, y: s.y }, 'node-badge err', '!', 'err');
    if (b.fcs) addBadge({ x: s.x, y: s.y + 52 }, 'node-badge err', 'FCS ✗', 'fcs');
  });
}

let badges = {};
function addBadge(s, cls, text, kind) {
  const el = document.createElement('span');
  el.className = cls;
  el.dataset.badge = kind;
  el.textContent = text;
  el.style.left = s.x + 'px';
  el.style.top = s.y + 'px';
  overlay.appendChild(el);
}

export function setBadges(map) {
  badges = map;
  renderOverlay();
}

export function clearMarkers() {
  markers.forEach((m) => m.el.remove());
  markers = [];
}

export function fit() {
  if (cy) { cy.resize(); cy.fit(undefined, 40); }
}
