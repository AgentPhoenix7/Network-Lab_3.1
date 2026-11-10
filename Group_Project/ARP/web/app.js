// App glue: wires the bridge (or the mock) to graph, inspector, dock and timeline.
import * as live from './ws-client.js';
import * as fake from './mock.js';
import { initGraph, loadTopology, setLit, pulseNode, spawnMarker, travelMs, clearMarkers, setBadges, selectEdgeOrNode, fit } from './graph.js';
import { foldAt, kindOf, summaryOf, nodePanel, lanPanel, edgePanel, packetPanel } from './panels.js';
import { histogram, eventTimes, nextTime, prevTime, toJsonl, fromJsonl } from './timeline.js';

const $ = (id) => document.getElementById(id);
const MC = 'ff:ff:ff:ff:ff:ff';
const WS_URL = 'ws://' + (location.host || 'localhost:8000') + '/ws';
const useMock = new URLSearchParams(location.search).get('mock') === '1';
const io = useMock ? fake : live;
const small = matchMedia('(max-width: 1100px)');

const S = {
  mode: 'run', topo: null, errors: [], events: [], run: null,
  cursor: 0, playing: false, sel: null, back: null,
  running: false, paused: false, conn: 'connecting', retry: null,
  speed: 1, pingDst: '', pingCount: 1, scope: 'all', rspd: 1,
  lit: {}, toastTimer: 0
};

const throttle = (fn, ms) => {
  let last = 0, timer = 0;
  return (...a) => {
    clearTimeout(timer);
    const wait = ms - (Date.now() - last);
    if (wait <= 0) { last = Date.now(); fn(...a); } else timer = setTimeout(() => { last = Date.now(); fn(...a); }, wait);
  };
};
const sendCmd = (cmd) => { if (!io.send(cmd)) toast('Not connected to the bridge'); };
const sendSpeed = throttle((speed) => sendCmd({ cmd: 'set_speed', speed }), 150);
// Values are read at input time: the trailing call of a throttle must not read a slider that a state echo already moved.
const impairNow = () => ({ lan: S.scope === 'all' ? null : +S.scope, loss: +$('loss').value / 100, corrupt: +$('corrupt').value / 100, delay_ms: +$('delay').value });
const sendImpair = throttle((c) => sendCmd({ cmd: 'set_impair', ...c }), 150);
const arpNow = () => ({ ttl_ms: +$('arp-ttl').value, retry_ms: +$('arp-retry').value, retries: +$('arp-retries').value });
const sendArp = throttle((c) => sendCmd({ cmd: 'set_arp', ...c }), 150);
// A slider the user is dragging keeps its thumb: the echo of an older value must not move it.
const setUnlessDragging = (el, v) => { if (document.activeElement !== el) el.value = v; };
const validateSoon = throttle(() => sendCmd({ cmd: 'validate', topology: S.topo }), 300);

/* ---------- helpers ---------- */
const speedToPos = (x) => Math.round(50 + 50 * Math.log10(x));
const posToSpeed = (v) => (Math.abs(v - 50) < 3 ? 1 : Math.pow(10, (v - 50) / 50));
const lastT = () => (S.events.length ? S.events[S.events.length - 1].t : 0);
const curT = () => (S.mode === 'replay' ? S.cursor : lastT());

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => (t.hidden = true), 4000);
}

function setMode(mode) {
  S.mode = mode;
  $('mode-edit').setAttribute('aria-pressed', mode === 'edit');
  $('mode-run').setAttribute('aria-pressed', mode === 'run');
  $('mode-replay').setAttribute('aria-pressed', mode === 'replay');
  $('tl-live').setAttribute('aria-pressed', mode === 'run');
  $('tl-replay').setAttribute('aria-pressed', mode === 'replay');
  $('btn-start').hidden = mode !== 'edit';
  $('btn-stop').hidden = mode === 'edit';
  $('edit-tools').hidden = mode !== 'edit';
  $('arp-timers').hidden = mode !== 'edit';
  requestRender();
}

function goEdit() {
  if (S.running) sendCmd({ cmd: 'stop' });
  S.running = false;
  setMode('edit');
}

function goRun() {
  if (S.mode === 'replay' && S.paused) sendCmd({ cmd: 'resume' });
  if (S.run === 'opened') { S.run = null; S.events = []; sendCmd({ cmd: 'get_log' }); }
  S.playing = false;
  setMode('run');
}

function goReplay() {
  if (S.mode !== 'replay' && S.running && !S.paused) sendCmd({ cmd: 'pause' });
  S.cursor = lastT();
  S.playing = false;
  setMode('replay');
}

/* ---------- messages from the bridge (or mock) ---------- */
function onMessage(msg) {
  switch (msg.type) {
    case 'hello': break;
    case 'topology':
      S.topo = JSON.parse(JSON.stringify(msg.topology));
      S.errors = [];
      loadTopology(S.topo);
      $('empty').hidden = true;
      requestRender();
      break;
    case 'validation':
      S.errors = (msg.errors || []).map(errorKey);
      toast(msg.ok ? 'Topology is valid' : S.errors.length + ' validation error' + (S.errors.length > 1 ? 's' : '') + ' — see the inspector');
      requestRender();
      break;
    case 'state': onState(msg); break;
    case 'event': onEvent(msg.event); break;
    case 'log':
      S.events = msg.events.slice();
      S.run = S.events.length ? S.events[S.events.length - 1].run : null;
      S.cursor = lastT();
      requestRender();
      break;
    case 'error':
      $('errbox').hidden = false;
      $('errbox').textContent = '✕ ' + msg.msg + ' · Stop and Start to restart · click to dismiss';
      $('errbox').title = msg.msg;
      break;
    default: break;
  }
}

// "nodes.h2.ifaces[0].ip" -> "h2:0:ip", the key the inspector fields use.
function errorKey(e) {
  const m = /^nodes\.([^.]+)\.ifaces\[(\d+)\]\.(\w+)/.exec(e.path || '');
  return { path: e.path, msg: e.msg, node: m ? m[1] : null, key: m ? m[1] + ':' + m[2] + ':' + m[3] : '' };
}

function onState(msg) {
  S.running = msg.running;
  S.paused = msg.paused;
  S.speed = msg.speed;
  setUnlessDragging($('speed'), speedToPos(msg.speed));
  $('speed-txt').textContent = '×' + (msg.speed < 1 ? msg.speed.toFixed(2) : msg.speed.toFixed(1));
  const g = msg.impair.global;
  setUnlessDragging($('loss'), Math.round(g.loss * 100));
  setUnlessDragging($('corrupt'), Math.round(g.corrupt * 100));
  setUnlessDragging($('delay'), g.delay_ms);
  $('loss-txt').textContent = $('loss').value + '%';
  $('corrupt-txt').textContent = $('corrupt').value + '%';
  $('delay-txt').textContent = $('delay').value + ' ms';
  if (msg.arp) { $('arp-ttl').value = msg.arp.ttl_ms; $('arp-retry').value = msg.arp.retry_ms; $('arp-retries').value = msg.arp.retries; }
  requestRender();
}

function onEvent(e) {
  if (S.run === 'opened') return; // a run opened from a file stays put; goRun() resyncs from the bridge
  if (e.run !== S.run) { S.run = e.run; S.events = []; }
  if (e.seq < S.events.length) return; // duplicate
  if (e.seq > S.events.length) { io.send({ cmd: 'get_log' }); return; } // gap: resync from the log
  S.events.push(e);
  if (S.mode !== 'replay') {
    S.cursor = e.t;
    if (S.mode === 'run') animate(e);
  }
  requestRender();
}

// Live animation: a medium event spawns one marker per receiver and lights its lines.
function animate(e) {
  if (e.ev === 'arp') pulseNode(e.node);
  if (e.ev !== 'medium') return;
  // The medium can overtake its tx (two telemetry sockets), so give the tx a moment to decide the marker's kind.
  if (S.events.some((x) => x.ev === 'tx' && x.uid === e.uid)) drawMedium(e);
  else setTimeout(() => { if (S.mode === 'run') drawMedium(e); }, 60);
}

function drawMedium(e) {
  const tx = S.events.find((x) => x.ev === 'tx' && x.uid === e.uid);
  const kind = kindOf(S.events, e.uid);
  const dur = travelMs(e.delay_ms, S.speed);
  e.to.forEach((r) => spawnMarker({ lan: e.lan, from: e.from, to: r.node, kind, fate: e.fate, ignored: isIgnored(e, tx, r), delayMs: e.delay_ms, speed: S.speed }));
  [e.from + ':' + e.from_iface, ...e.to.map((r) => r.node + ':' + r.iface)].forEach((id) => (S.lit[id] = Date.now() + dur));
  setTimeout(requestRender, dur + 20);
}

// Bus only: a unicast frame reaches every station, and only its target accepts it.
function isIgnored(e, tx, r) {
  if (!tx?.decoded?.eth || S.topo.lans[e.lan].mode === 'star') return false; // a frame that failed to decode is not "ignored"
  const dst = tx.decoded.eth.dst;
  if (dst === MC) return false;
  const n = S.topo.nodes.find((x) => x.id === r.node);
  return !!n && dst !== n.ifaces[r.iface].mac;
}

/* ---------- replay: markers and lines come from the cursor, not the clock ---------- */
function renderReplayFrame() {
  clearMarkers();
  const lit = [];
  S.events.forEach((e) => {
    if (e.ev !== 'medium' || S.cursor < e.t || S.cursor >= e.t + e.delay_ms) return;
    const tx = S.events.find((x) => x.ev === 'tx' && x.uid === e.uid);
    const p = (S.cursor - e.t) / e.delay_ms;
    const kind = kindOf(S.events, e.uid);
    e.to.forEach((r) => spawnMarker({ lan: e.lan, from: e.from, to: r.node, kind, fate: e.fate, ignored: isIgnored(e, tx, r), delayMs: e.delay_ms, progress: p }));
    lit.push(e.from + ':' + e.from_iface, ...e.to.map((r) => r.node + ':' + r.iface));
  });
  setLit(lit);
}

/* ---------- rendering ---------- */
let renderQueued = false;
function requestRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderAll(); });
}

function renderAll() {
  const t = curT();
  $('empty').hidden = !!S.topo;
  renderStatus();
  renderBadges(t);
  if (S.mode === 'replay') renderReplayFrame();
  else {
    const now = Date.now();
    setLit(Object.keys(S.lit).filter((k) => S.lit[k] > now));
  }
  renderLog(t);
  renderTimeline(t);
  renderInspector(t);
}

function renderStatus() {
  let text = 'Stopped', dot = '';
  if (S.conn !== 'connected') { text = S.conn === 'connecting' ? 'Connecting' : 'Disconnected'; dot = 'bad'; }
  else if (S.mode === 'edit') text = 'Stopped';
  else if (S.mode === 'replay') { text = 'Replay'; dot = 'replay'; }
  else if (S.running) { text = S.paused ? 'Paused' : 'Running'; dot = S.paused ? 'pause' : 'run'; }
  $('st-text').textContent = text;
  $('st-dot').className = 'dot ' + dot;
  $('st-sub').textContent = useMock ? 'mock · ' + WS_URL : WS_URL;
  $('btn-pause').textContent = S.paused ? '▶ Resume' : '❚❚ Pause';
  $('btn-pause').setAttribute('aria-label', S.paused ? 'Resume' : 'Pause');
  const playing = S.mode === 'replay' && S.playing;
  $('tl-play').textContent = playing ? '❚❚' : '▶';
}

function renderBadges(t) {
  const fo = foldAt(S.events, t);
  const map = {};
  Object.keys(fo.caches).forEach((n) => {
    const pend = Object.values(fo.caches[n]).filter((c) => c.state === 'PENDING').length;
    if (pend) map[n] = Object.assign(map[n] || {}, { pending: pend });
  });
  S.events.forEach((e) => {
    if (e.ev === 'rx' && e.verdict === 'fcs_fail' && t >= e.t && t < e.t + 600) map[e.node] = Object.assign(map[e.node] || {}, { fcs: true });
  });
  S.errors.forEach((e) => { if (e.node) map[e.node] = Object.assign(map[e.node] || {}, { err: true }); });
  setBadges(map);
  selectEdgeOrNode(S.sel);
}

// Channels report `lan`, stations report `node`; hello and ready come from both.
const who = (e) => e.node || 'LAN ' + String.fromCharCode(65 + e.lan);

function logText(e) {
  if (e.ev === 'ready') return who(e) + ' ready';
  if (e.ev === 'hello') return who(e) + ' started';
  if (e.ev === 'switch_learn') return 'LAN ' + String.fromCharCode(65 + e.lan) + ': ' + e.mac + ' is on ' + e.node + '/if' + e.iface;
  if (e.ev === 'ip') return e.node + ' ' + e.op + ' ' + (e.icmp === 'echo_req' ? 'echo' : 'reply') + ' ' + e.src + ' → ' + e.dst;
  if (e.ev === 'arp') return e.node + '/if' + e.iface + ' ' + e.op + ' ' + e.ip + (e.mac ? ' = ' + e.mac : '');
  if (e.ev === 'tx') return e.node + ' sends ' + summaryOf(e);
  if (e.ev === 'medium') return 'LAN ' + String.fromCharCode(65 + e.lan) + ': ' + e.uid + ' → ' + e.to.map((r) => r.node).join(', ') + ' (' + e.fate + ')';
  if (e.ev === 'rx') return e.node + ' ' + e.verdict + ' ' + e.uid;
  return e.ev;
}
const logChip = (e) => (e.ev === 'arp' ? 'amber' : e.ev === 'ip' ? 'blue' : e.ev === 'tx' ? (e.decoded?.arp ? 'amber' : 'blue') : e.ev === 'rx' ? (e.verdict === 'accepted' ? 'green' : 'mute') : e.ev === 'medium' ? (e.fate === 'ok' ? 'teal' : 'red') : 'mute');

function renderLog(t) {
  const rows = S.events.filter((e) => e.t <= t).slice(-40).reverse();
  $('log-count').textContent = S.events.length + ' events';
  $('log-list').innerHTML = rows.map((e, i) => `<div class="log-row ${i === 0 ? 'new' : ''}"><span class="t">${(e.t / 1000).toFixed(3)}</span>
    <div><span class="chip ${logChip(e)}">${e.ev}</span><div class="txt">${escape(logText(e))}</div></div></div>`).join('');
}

function escape(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

function renderTimeline(t) {
  const end = lastT();
  $('hist').innerHTML = histogram(S.events, end || 1).map((h, i) => `<div style="opacity:${i * (end || 1) / 20 <= t ? 1 : 0.4}"><i style="height:${h.err}px;background:var(--red)"></i><i style="height:${h.ip}px;background:var(--blue)"></i><i style="height:${h.arp}px;background:var(--amber)"></i></div>`).join('');
  $('scrub').value = end ? Math.round((t / end) * 1000) : 0;
  $('tl-t').textContent = 't = ' + (t / 1000).toFixed(3) + ' s';
}

function renderInspector(t) {
  const body = $('insp-body');
  const f = document.activeElement;
  if (f && body.contains(f) && (f.tagName === 'SELECT' || f.tagName === 'INPUT')) return; // do not close an open list or eat typing
  if (!S.topo) { body.innerHTML = '<div class="sec muted">No topology yet.</div>'; return; }
  const ctx = { topo: S.topo, events: S.events, t, mode: S.mode, errors: S.errors, pingDst: S.pingDst, pingCount: S.pingCount, scope: S.scope };
  const sel = S.sel;
  let h = '';
  if (!sel) h = '<div class="sec muted">Click a node, a LAN bar or a line to inspect it.</div>';
  else if (sel.kind === 'node') h = nodePanel(ctx, sel.id);
  else if (sel.kind === 'lan') h = lanPanel(ctx, sel.id);
  else if (sel.kind === 'edge') h = edgePanel(ctx, sel.id);
  else if (sel.kind === 'packet') h = packetPanel(ctx, sel.id);
  body.innerHTML = h;
}

/* ---------- inspector interaction ---------- */
function select(sel) {
  S.back = S.sel;
  S.sel = sel;
  if (small.matches) $('insp').classList.add('open');
  requestRender();
}

function onInspectorClick(ev) {
  const el = ev.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  if (act === 'ping') {
    S.pingDst = $('ping-dst').value;
    S.pingCount = +$('ping-count').value;
    sendCmd({ cmd: 'ping', node: el.dataset.node, dst: S.pingDst, count: S.pingCount });
    goRun();
  } else if (act === 'flush') sendCmd({ cmd: 'flush', node: el.dataset.node });
  else if (act === 'override') {
    const lan = el.dataset.lan;
    S.scope = S.scope === lan ? 'all' : lan;
    $('scope').value = S.scope;
    requestRender();
  } else if (act === 'packet') select({ kind: 'packet', id: el.dataset.uid });
  else if (act === 'back') { S.sel = S.back || null; requestRender(); }
}

function onInspectorInput(ev) {
  const el = ev.target;
  if (el.id === 'ping-dst') S.pingDst = el.value;
  if (el.id === 'ping-count') S.pingCount = +el.value;
  if (!el.dataset.edit) return;
  const [node, idx, field] = el.dataset.edit.split('|');
  const iface = S.topo.nodes.find((n) => n.id === node).ifaces[+idx];
  iface[field] = el.value;
  S.errors = S.errors.filter((e) => e.key !== node + ':' + idx + ':' + field);
  validateSoon();
}

// Hovering a field in the packet grid lights its bytes in the hex dump, and the reverse.
function onHover(ev) {
  $('insp-body').querySelectorAll('.on').forEach((x) => x.classList.remove('on')); // grid cells and hex bytes
  const dump = $('hexdump');
  if (!dump) return;
  const gc = ev.target.closest && ev.target.closest('.gc[data-off]');
  const hx = ev.target.closest && ev.target.closest('.hx[data-i]');
  if (gc) {
    const off = +gc.dataset.off, len = +gc.dataset.len;
    gc.classList.add('on');
    dump.querySelectorAll('.hx').forEach((b) => { const i = +b.dataset.i; if (i >= off && i < off + len) b.classList.add('on'); });
  } else if (hx) {
    const i = +hx.dataset.i;
    hx.classList.add('on');
    dump.querySelectorAll('.gc[data-off]').forEach((c) => { const o = +c.dataset.off; if (i >= o && i < o + +c.dataset.len) c.classList.add('on'); });
  }
}

/* ---------- graph clicks, top bar, dock, timeline ---------- */
function onGraphSelect(sel) {
  if (sel.kind === 'node' && S.sel && S.sel.kind === 'packet') S.back = S.sel;
  select(sel);
}

function bindUi() {
  $('mode-edit').onclick = goEdit;
  $('mode-run').onclick = goRun;
  $('mode-replay').onclick = goReplay;
  $('tl-live').onclick = goRun;
  $('tl-replay').onclick = goReplay;
  $('preset').onchange = (e) => { sendCmd({ cmd: 'load_preset', name: e.target.value }); };
  $('btn-pick').onclick = () => { $('preset').value = 'proxy_arp'; sendCmd({ cmd: 'load_preset', name: 'proxy_arp' }); };
  $('btn-validate').onclick = () => sendCmd({ cmd: 'validate', topology: S.topo });
  $('btn-start').onclick = () => {
    if (!S.topo) return toast('Pick a preset first');
    sendCmd({ cmd: 'start', topology: S.topo });
    S.running = true;
    setMode('run');
  };
  $('btn-stop').onclick = goEdit;
  initPanels();
  $('btn-pause').onclick = () => sendCmd({ cmd: S.paused ? 'resume' : 'pause' });
  document.querySelectorAll('[data-todo]').forEach((b) => (b.onclick = () => toast(b.dataset.todo + ' is not in contract §3.2 yet')));
  $('speed').oninput = (e) => { const v = +e.target.value; $('speed').value = Math.abs(v - 50) < 3 ? 50 : v; S.speed = posToSpeed(+$('speed').value); $('speed-txt').textContent = '×' + (S.speed < 1 ? S.speed.toFixed(2) : S.speed.toFixed(1)); sendSpeed(S.speed); };
  $('loss').oninput = (e) => { $('loss-txt').textContent = e.target.value + '%'; sendImpair(impairNow()); };
  $('corrupt').oninput = (e) => { $('corrupt-txt').textContent = e.target.value + '%'; sendImpair(impairNow()); };
  $('delay').oninput = (e) => { $('delay-txt').textContent = e.target.value + ' ms'; sendImpair(impairNow()); };
  $('scope').onchange = (e) => { S.scope = e.target.value; requestRender(); };
  ['arp-ttl', 'arp-retry', 'arp-retries'].forEach((id) => ($(id).oninput = () => sendArp(arpNow())));
  $('tl-start').onclick = () => { S.cursor = 0; S.playing = false; if (S.mode !== 'replay') goReplay(); requestRender(); };
  $('tl-prev').onclick = () => { const p = prevTime(eventTimes(S.events), S.cursor); if (S.mode !== 'replay') goReplay(); S.cursor = p === undefined ? 0 : p; S.playing = false; requestRender(); };
  $('tl-next').onclick = () => { const n = nextTime(eventTimes(S.events), S.cursor); if (S.mode !== 'replay') goReplay(); S.cursor = n === undefined ? lastT() : n; S.playing = false; requestRender(); };
  $('tl-play').onclick = () => { if (S.mode !== 'replay') goReplay(); if (S.cursor >= lastT()) S.cursor = 0; S.playing = !S.playing; requestRender(); };
  $('scrub').oninput = (e) => { if (S.mode !== 'replay') goReplay(); S.playing = false; S.cursor = (+e.target.value / 1000) * lastT(); requestRender(); };
  $('rspd').onchange = (e) => { S.rspd = +e.target.value; };
  $('btn-export').onclick = () => {
    const url = URL.createObjectURL(new Blob([toJsonl(S.events)], { type: 'application/x-ndjson' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'run.jsonl'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Exported run.jsonl · ' + S.events.length + ' events');
  };
  $('btn-open').onclick = () => $('file-open').click();
  $('file-open').onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      S.events = fromJsonl(await f.text());
      S.run = 'opened';
      S.cursor = 0;
      setMode('replay');
      if (S.mode === 'replay') S.playing = false;
      S.running = false;
      S.paused = true;
      toast('Opened ' + f.name + ' · ' + S.events.length + ' events (no server needed)');
    } catch (err) {
      toast('Could not open: ' + err.message);
    }
    e.target.value = '';
  };
  $('insp-body').addEventListener('click', onInspectorClick);
  $('insp-body').addEventListener('input', onInspectorInput);
  $('insp-body').addEventListener('change', (e) => { if (e.target.tagName === 'SELECT') e.target.blur(); }); // chosen: let the panel refresh again
  $('insp-body').addEventListener('focusout', requestRender);
  $('insp-body').addEventListener('mouseover', onHover);
  $('insp-body').addEventListener('mouseleave', () => onHover({ target: {} }), true);
  $('errbox').onclick = () => ($('errbox').hidden = true);
}

// Replay playback: moves the cursor by the replay speed every 40 ms.
function startReplayLoop() {
  setInterval(() => {
    if (S.mode !== 'replay' || !S.playing) return;
    const end = lastT();
    S.cursor = Math.min(end, S.cursor + 40 * S.rspd);
    if (S.cursor >= end) S.playing = false;
    requestRender();
  }, 40);
}

function onConnStatus(state, info = {}) {
  S.conn = state;
  const banner = $('banner');
  banner.hidden = state === 'connected';
  banner.classList.toggle('bad', state !== 'connected');
  if (state === 'disconnected') banner.textContent = `Connection to ${WS_URL} lost. Retrying in ${Math.round(info.delay / 1000)} s (attempt ${info.attempt}). Your replay log is kept in memory.`;
  else if (state === 'connecting') { banner.hidden = false; banner.textContent = 'Connecting to ' + WS_URL + '…'; }
  if (state === 'connected') io.send({ cmd: 'get_log' });
  requestRender();
}

function init() {
  initGraph($('cy'), onGraphSelect);
  bindUi();
  io.onMessage(onMessage);
  io.onStatus(onConnStatus);
  startReplayLoop();
  small.addEventListener('change', requestRender);
  window.addEventListener('resize', fit);
  io.connect(WS_URL);
  requestRender();
}

init();

/* ---------- side panels: drag the bar to resize, the buttons hide them (choice is remembered) ---------- */
function initPanels() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('arp.panels')) || {}; } catch (e) { /* no storage: sizes reset on reload */ }
  const save = () => { try { localStorage.setItem('arp.panels', JSON.stringify(saved)); } catch (e) { /* ignore */ } };
  const apply = () => {
    const root = document.documentElement.style;
    if (saved.logW) root.setProperty('--log-w', saved.logW + 'px');
    if (saved.inspW) root.setProperty('--insp-w', saved.inspW + 'px');
    [['log', 'rz-log', 'btn-log'], ['insp', 'rz-insp', 'btn-insp']].forEach(([id, rz, btn]) => {
      const hidden = !!saved[id + 'Hidden'];
      $(id).classList.toggle('collapsed', hidden);
      $(rz).hidden = hidden;
      $(btn).setAttribute('aria-pressed', String(!hidden));
    });
  };
  const toggle = (id) => {
    if (small.matches) { $(id).classList.toggle('open'); return; } // narrow screens: the panels are drawers
    saved[id + 'Hidden'] = !saved[id + 'Hidden'];
    save(); apply();
  };
  const resize = (id, w) => {
    const other = $(id === 'log' ? 'insp' : 'log').offsetWidth;
    saved[id + 'W'] = Math.round(Math.max(180, Math.min(w, 720, window.innerWidth - other - 360)));
    apply();
  };
  const grab = (rz, id) => {
    const fromEdge = (e) => (id === 'log' ? e.clientX - $(id).getBoundingClientRect().left : $(id).getBoundingClientRect().right - e.clientX);
    rz.onpointerdown = (e) => { rz.setPointerCapture(e.pointerId); rz.classList.add('drag'); document.body.style.userSelect = 'none'; };
    rz.onpointermove = (e) => { if (rz.classList.contains('drag')) resize(id, fromEdge(e)); };
    rz.onpointerup = () => { rz.classList.remove('drag'); document.body.style.userSelect = ''; save(); };
    rz.onkeydown = (e) => {
      const d = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
      if (!d) return;
      e.preventDefault();
      resize(id, $(id).offsetWidth + (id === 'log' ? d : -d));
      save();
    };
  };
  $('btn-log').onclick = $('log-hide').onclick = () => toggle('log');
  $('btn-insp').onclick = $('insp-hide').onclick = () => toggle('insp');
  grab($('rz-log'), 'log');
  grab($('rz-insp'), 'insp');
  apply();
}
