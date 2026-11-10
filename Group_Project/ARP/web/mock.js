// Fake bridge for ?mock=1. Same four exports as ws-client.js, no WebSocket anywhere.
// Serves the proxy_arp topology and plays a scripted run per ping (contract §3, §4).

const MC = 'ff:ff:ff:ff:ff:ff';
const ZERO = '00:00:00:00:00:00';
const H1MAC = '02:00:00:00:01:00';
const MAC = { h1: H1MAC, h2: '02:00:00:00:02:00', r0: '02:00:00:00:03:00', r1: '02:00:00:00:03:01', h3: '02:00:00:00:04:00', h4: '02:00:00:00:05:00' };
const IP = { h1: '141.23.56.10', h2: '141.23.56.11', r0: '141.23.56.1', r1: '141.23.56.17', h3: '141.23.56.21', h4: '141.23.56.22' };

const topology = {
  lans: [
    { id: 0, name: 'LAN A', mode: 'bus', cidr: '141.23.56.0/24', x: 245, y: 300 },
    { id: 1, name: 'LAN B', mode: 'star', cidr: '141.23.56.16/29', x: 610, y: 440 }
  ],
  nodes: [
    { id: 'h1', kind: 'host', x: 110, y: 150, gateway: null, ifaces: [{ idx: 0, lan: 0, ip: IP.h1, prefix: 24, mac: MAC.h1 }] },
    { id: 'h2', kind: 'host', x: 250, y: 150, gateway: null, ifaces: [{ idx: 0, lan: 0, ip: IP.h2, prefix: 24, mac: MAC.h2 }] },
    { id: 'r1', kind: 'router', x: 400, y: 440, gateway: null, routes: [], ifaces: [
      { idx: 0, lan: 0, ip: IP.r0, prefix: 24, mac: MAC.r0, proxy: ['141.23.56.21/32', '141.23.56.22/32', '141.23.56.23/32'] },
      { idx: 1, lan: 1, ip: IP.r1, prefix: 29, mac: MAC.r1 }] },
    { id: 'h3', kind: 'host', x: 720, y: 330, gateway: IP.r1, ifaces: [{ idx: 0, lan: 1, ip: IP.h3, prefix: 29, mac: MAC.h3 }] },
    { id: 'h4', kind: 'host', x: 720, y: 550, gateway: IP.r1, ifaces: [{ idx: 0, lan: 1, ip: IP.h4, prefix: 29, mac: MAC.h4 }] }
  ]
};

/* ---- frame bytes (lab1 layout, 65 B): header 17 | payload 44 | FCS 4 ---- */
const macBytes = (s) => s.split(':').map((h) => parseInt(h, 16));
const ipBytes = (s) => s.split('.').map(Number);
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function cks16(bytes) {
  let s = 0;
  for (let i = 0; i < bytes.length; i += 2) s += (bytes[i] << 8) + (bytes[i + 1] || 0);
  while (s >> 16) s = (s & 0xffff) + (s >> 16);
  return ~s & 0xffff;
}
function frameHex(d) {
  const b = new Array(65).fill(0);
  const put = (off, bytes) => bytes.forEach((v, i) => (b[off + i] = v & 255));
  put(0, [d.eth.type === 'ARP' ? 0x06 : 0x08]);
  put(1, macBytes(d.eth.src));
  put(7, macBytes(d.eth.dst));
  put(13, [d.eth.len >> 8, d.eth.len & 255, d.eth.seq >> 8, d.eth.seq & 255]);
  if (d.arp) {
    const a = d.arp;
    put(17, [0, 1, 8, 0, 6, 4, 0, a.oper === 'request' ? 1 : 2]);
    put(25, macBytes(a.sha)); put(31, ipBytes(a.spa));
    put(35, macBytes(a.tha)); put(41, ipBytes(a.tpa));
  } else {
    const p = d.ip, ic = d.icmp;
    const h = [0x45, 0, 0, 44, p.id >> 8, p.id & 255, 0, 0, p.ttl, p.proto, 0, 0, ...ipBytes(p.src), ...ipBytes(p.dst)];
    const hc = cks16(h); h[10] = hc >> 8; h[11] = hc & 255;
    put(17, h);
    const data = [...('ARPLAB-PING-' + ('000' + ic.seq).slice(-4))].map((c) => c.charCodeAt(0));
    const im = [ic.type === 'echo_req' ? 8 : 0, 0, 0, 0, 0, ic.id, ic.seq >> 8, ic.seq & 255, ...data];
    const ik = cks16(im); im[2] = ik >> 8; im[3] = ik & 255;
    put(37, im);
  }
  const f = crc32(b.slice(0, 61));
  put(61, [f >>> 24, (f >>> 16) & 255, (f >>> 8) & 255, f & 255]);
  return { hex: b.map((v) => v.toString(16).padStart(2, '0')).join(''), fcs: '0x' + f.toString(16).padStart(8, '0') };
}

/* ---- scripted run: ARP resolution of h3 (proxy), then ping echoes ---- */
function buildRun(count) {
  const evs = [];
  const nic = {};
  let t = 0;
  let u = '';
  const add = (o) => evs.push(Object.assign({ t }, o));
  const at = (when, fn) => { const save = t; t = when; fn(); t = save; };
  const seqOf = (node, iface) => { const k = node + iface; nic[k] = nic[k] || 0; return nic[k]++; };
  // Builds the frame, emits tx with hex and decoded fields (contract §3.1)
  const tx = (node, iface, decoded) => {
    const s = seqOf(node, iface);
    decoded.eth.seq = s;
    const f = frameHex(decoded);
    decoded.eth.fcs = f.fcs;
    add({ ev: 'tx', node, iface, uid: node + ':' + iface + ':' + s, hex: f.hex, decoded });
    return node + ':' + iface + ':' + s;
  };
  const medium = (lan, uid, from, fromIface, to, fate) => add({ ev: 'medium', lan, uid, from, from_iface: fromIface, to, fate, delay_ms: 300 });
  const rx = (node, iface, uid, verdict) => add({ ev: 'rx', node, iface, uid, verdict });
  const arp = (node, iface, o) => add(Object.assign({ ev: 'arp', node, iface, ttl_ms: 0, attempts: 0, queued: 0, mac: null, state: null }, o));
  const ipEv = (node, o) => add(Object.assign({ ev: 'ip', node }, o));
  const arpPkt = (sha, spa, tha, tpa, oper) => ({ eth: { type: 'ARP', src: sha, dst: oper === 'request' ? MC : tha, len: 28 }, arp: { htype: 1, ptype: '0x0800', hlen: 6, plen: 4, oper, sha, spa, tha, tpa } });
  const ipPkt = (srcMac, dstMac, src, dst, type, k) => ({ eth: { type: 'IPv4', src: srcMac, dst: dstMac, len: 44 }, ip: { ttl: 64, proto: 1, src, dst, id: k + 1 }, icmp: { type, id: 1, seq: k } });

  ['h1', 'h2', 'r1', 'h3', 'h4'].forEach((n) => add({ ev: 'ready', node: n }));
  // h1 needs 141.23.56.21 on LAN A (bus); r1 proxies for h3
  t = 10;
  ipEv('h1', { op: 'send', src: IP.h1, dst: IP.h3, icmp: 'echo_req', icmp_seq: 0 });
  arp('h1', 0, { op: 'pending', ip: IP.h3, state: 'PENDING', attempts: 1 });
  arp('h1', 0, { op: 'queued', ip: IP.h3, state: 'PENDING', attempts: 1, queued: 1 });
  u = tx('h1', 0, arpPkt(MAC.h1, IP.h1, ZERO, IP.h3, 'request'));
  medium(0, u, 'h1', 0, [{ node: 'h2', iface: 0 }, { node: 'r1', iface: 0 }], 'ok');
  at(310, () => {
    rx('h2', 0, u, 'accepted'); rx('r1', 0, u, 'accepted');
    arp('r1', 0, { op: 'learn', ip: IP.h1, mac: MAC.h1, state: 'RESOLVED', ttl_ms: 30000 });
    arp('r1', 0, { op: 'proxy_reply', ip: IP.h3, mac: MAC.r0 });
    u = tx('r1', 0, arpPkt(MAC.r0, IP.h3, MAC.h1, IP.h1, 'reply'));
    medium(0, u, 'r1', 0, [{ node: 'h1', iface: 0 }, { node: 'h2', iface: 0 }], 'ok');
  });
  at(610, () => {
    rx('h2', 0, u, 'ignored'); rx('h1', 0, u, 'accepted');
    arp('h1', 0, { op: 'resolved', ip: IP.h3, mac: MAC.r0, state: 'RESOLVED', ttl_ms: 30000, attempts: 1 });
  });
  for (let k = 0; k < count; k++) echo(k, k === 0 ? 610 : 2410 + (k - 1) * 1000, k === 0);
  return evs.sort((a, b) => a.t - b.t);

  // One echo round trip starting with h1's transmit at t0. The first one also runs ARP on LAN B.
  function echo(k, t0, first) {
    if (!first) at(t0, () => ipEv('h1', { op: 'send', src: IP.h1, dst: IP.h3, icmp: 'echo_req', icmp_seq: k }));
    at(t0, () => {
      u = tx('h1', 0, ipPkt(MAC.h1, MAC.r0, IP.h1, IP.h3, 'echo_req', k));
      medium(0, u, 'h1', 0, [{ node: 'h2', iface: 0 }, { node: 'r1', iface: 0 }], 'ok');
    });
    at(t0 + 300, () => {
      rx('h2', 0, u, 'ignored'); rx('r1', 0, u, 'accepted');
      ipEv('r1', { op: 'forward', src: IP.h1, dst: IP.h3, icmp: 'echo_req', icmp_seq: k });
    });
    let tLan = t0 + 300;
    if (first) {
      // r1 has no LAN B entry for h3 yet: ARP on the star, then h3 answers
      at(910, () => {
        arp('r1', 1, { op: 'pending', ip: IP.h3, state: 'PENDING', attempts: 1 });
        arp('r1', 1, { op: 'queued', ip: IP.h3, state: 'PENDING', attempts: 1, queued: 1 });
        u = tx('r1', 1, arpPkt(MAC.r1, IP.r1, ZERO, IP.h3, 'request'));
        add({ ev: 'switch_learn', lan: 1, mac: MAC.r1, node: 'r1', iface: 1 });
        medium(1, u, 'r1', 1, [{ node: 'h3', iface: 0 }, { node: 'h4', iface: 0 }], 'ok');
      });
      at(1210, () => {
        rx('h3', 0, u, 'accepted'); rx('h4', 0, u, 'accepted');
        arp('h3', 0, { op: 'learn', ip: IP.r1, mac: MAC.r1, state: 'RESOLVED', ttl_ms: 30000 });
        arp('h3', 0, { op: 'reply_sent', ip: IP.h3, mac: MAC.h3 });
        u = tx('h3', 0, arpPkt(MAC.h3, IP.h3, MAC.r1, IP.r1, 'reply'));
        add({ ev: 'switch_learn', lan: 1, mac: MAC.h3, node: 'h3', iface: 0 });
        medium(1, u, 'h3', 0, [{ node: 'r1', iface: 1 }], 'ok');
      });
      at(1510, () => {
        rx('r1', 1, u, 'accepted');
        arp('r1', 1, { op: 'resolved', ip: IP.h3, mac: MAC.h3, state: 'RESOLVED', ttl_ms: 30000, attempts: 1, queued: 0 });
      });
      tLan = 1510;
    }
    // r1 sends on LAN B: known unicast to h3 only (star)
    at(tLan, () => {
      u = tx('r1', 1, ipPkt(MAC.r1, MAC.h3, IP.h1, IP.h3, 'echo_req', k));
      medium(1, u, 'r1', 1, [{ node: 'h3', iface: 0 }], 'ok');
    });
    at(tLan + 300, () => {
      rx('h3', 0, u, 'accepted');
      ipEv('h3', { op: 'deliver', src: IP.h1, dst: IP.h3, icmp: 'echo_req', icmp_seq: k });
      u = tx('h3', 0, ipPkt(MAC.h3, MAC.r1, IP.h3, IP.h1, 'echo_rep', k));
      medium(1, u, 'h3', 0, [{ node: 'r1', iface: 1 }], 'ok');
    });
    at(tLan + 600, () => {
      rx('r1', 1, u, 'accepted');
      ipEv('r1', { op: 'forward', src: IP.h3, dst: IP.h1, icmp: 'echo_rep', icmp_seq: k });
      u = tx('r1', 0, ipPkt(MAC.r0, MAC.h1, IP.h3, IP.h1, 'echo_rep', k));
      medium(0, u, 'r1', 0, [{ node: 'h1', iface: 0 }, { node: 'h2', iface: 0 }], 'ok');
    });
    at(tLan + 900, () => {
      rx('h2', 0, u, 'ignored'); rx('h1', 0, u, 'accepted');
      ipEv('h1', { op: 'deliver', src: IP.h3, dst: IP.h1, icmp: 'echo_rep', icmp_seq: k });
    });
  }
}

/* ---- fake server state ---- */
const listeners = [];
const statusListeners = [];
const state = { running: false, paused: false, speed: 1, impair: { global: { loss: 0, corrupt: 0, delay_ms: 300 }, per_lan: {} }, arp: { ttl_ms: 30000, retry_ms: 1000, retries: 0 } };
let events = [];
let queue = [];
let simNow = 0;
let runId = '';
let timer = null;
let lastTick = 0;

const emit = (msg) => listeners.forEach((h) => h(msg));
const sendState = () => emit({ type: 'state', running: state.running, paused: state.paused, speed: state.speed, impair: state.impair, arp: state.arp });

function startRun(count) {
  runId = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
  events = [];
  queue = buildRun(count).map((e, i) => Object.assign(e, { seq: i, run: runId }));
  simNow = 0;
  state.running = true;
  state.paused = false;
  sendState();
}

// Sim clock advances by real elapsed ms times speed; speed 0 or paused freezes it.
function pump() {
  const now = Date.now();
  const dt = now - lastTick;
  lastTick = now;
  if (!state.running || state.paused || state.speed === 0) return;
  simNow += dt * state.speed;
  while (queue.length && queue[0].t <= simNow) {
    const e = queue.shift();
    events.push(e);
    emit({ type: 'event', event: e });
  }
  if (!queue.length) { state.running = false; sendState(); }
}

function ensureTimer() {
  if (!timer) { lastTick = Date.now(); timer = setInterval(pump, 25); }
}

function validate(topo) {
  const seen = {}, errors = [];
  topo.nodes.forEach((n) => n.ifaces.forEach((f) => {
    const path = 'nodes.' + n.id + '.ifaces[' + f.idx + '].ip';
    if (seen[f.ip]) errors.push({ path, msg: 'duplicate IP ' + f.ip });
    seen[f.ip] = 1;
  }));
  return errors;
}

function handle(cmd) {
  switch (cmd.cmd) {
    case 'validate': {
      const errors = validate(cmd.topology || topology);
      emit({ type: 'validation', ok: errors.length === 0, errors });
      break;
    }
    case 'start': startRun(1); ensureTimer(); break;
    case 'stop': state.running = false; queue = []; sendState(); break;
    case 'pause': state.paused = true; sendState(); break;
    case 'resume': state.paused = false; sendState(); break;
    case 'set_speed': state.speed = Math.min(10, Math.max(0.1, cmd.speed)); sendState(); break;
    case 'set_impair':
      if (cmd.lan == null) state.impair.global = { loss: cmd.loss, corrupt: cmd.corrupt, delay_ms: cmd.delay_ms };
      else if (cmd.clear) delete state.impair.per_lan[cmd.lan];
      else state.impair.per_lan[cmd.lan] = { loss: cmd.loss, corrupt: cmd.corrupt, delay_ms: cmd.delay_ms };
      sendState(); break;
    case 'set_arp': state.arp = { ttl_ms: cmd.ttl_ms, retry_ms: cmd.retry_ms, retries: cmd.retries }; sendState(); break;
    case 'ping': startRun(Math.max(1, Math.min(5, cmd.count || 1))); ensureTimer(); break;
    case 'flush': {
      const e = { ev: 'arp', node: cmd.node, iface: 0, op: 'flush', ip: null, mac: null, state: null, ttl_ms: 0, attempts: 0, queued: 0, t: Math.round(simNow), run: runId, seq: events.length };
      events.push(e);
      emit({ type: 'event', event: e });
      break;
    }
    case 'get_log': emit({ type: 'log', events: events.slice() }); break;
    case 'load_preset': emit({ type: 'topology', topology }); sendState(); break;
    default: emit({ type: 'error', msg: 'mock: unknown command ' + cmd.cmd });
  }
}

export function connect() {
  statusListeners.forEach((h) => h('connected', { attempt: 0 }));
  emit({ type: 'hello', version: 1 });
  emit({ type: 'topology', topology });
  sendState();
}

export function send(cmdObject) {
  handle(cmdObject);
  return true;
}

export function onMessage(handler) { listeners.push(handler); }
export function onStatus(handler) { statusListeners.push(handler); }
