// Inspector panels (node, LAN, edge, packet) and the state fold used by them.
// Every dynamic string goes through esc(); values come from the contract events, never from innerHTML of raw input.

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtS = (ms) => (ms / 1000).toFixed(3);

// Folds all events with t <= t into ARP caches (per node), star MAC tables and per-LAN frame counters.
export function foldAt(events, t) {
  const caches = {}, macs = {}, counts = {};
  events.forEach((e) => {
    if (e.t > t) return;
    if (e.ev === 'arp') {
      const c = (caches[e.node] = caches[e.node] || {});
      if (['pending', 'queued', 'retry'].includes(e.op)) c[e.ip] = { ip: e.ip, mac: null, state: 'PENDING', attempts: e.attempts, queued: e.queued, t0: e.t, ttl: 0 };
      else if (e.op === 'resolved' || e.op === 'learn') c[e.ip] = { ip: e.ip, mac: e.mac, state: 'RESOLVED', attempts: e.attempts, queued: 0, t0: e.t, ttl: e.ttl_ms };
      else if (['fail', 'expire', 'flush'].includes(e.op)) { if (e.op === 'flush') caches[e.node] = {}; else delete c[e.ip]; }
    }
    if (e.ev === 'switch_learn') (macs[e.lan] = macs[e.lan] || {})[e.mac] = e.node + ':if' + e.iface;
    if (e.ev === 'medium') {
      const k = counts[e.lan] = counts[e.lan] || { ok: 0, lost: 0, cor: 0 };
      if (e.fate === 'ok') k.ok++; else if (e.fate === 'lost') k.lost++; else k.cor++;
    }
  });
  return { caches, macs, counts };
}

// Kind of a frame: 'req', 'rep', 'proxy' (a reply sent on behalf of another IP) or 'ip'.
export function kindOf(events, uid) {
  // The medium event can arrive before its tx (two telemetry sockets), and a tx may have no decoded part.
  const tx = events.find((e) => e.ev === 'tx' && e.uid === uid);
  if (!tx?.decoded?.arp) return 'ip';
  const a = tx.decoded.arp;
  if (a.oper === 'request') return 'req';
  const px = events.some((e) => e.ev === 'arp' && e.op === 'proxy_reply' && e.node === tx.node && e.ip === a.spa);
  return px ? 'proxy' : 'rep';
}

export function summaryOf(tx) {
  const d = tx.decoded;
  if (!d) return 'frame (not decoded)';
  if (d.arp) return d.arp.oper === 'request' ? 'ARP request: who has ' + d.arp.tpa + '? tell ' + d.arp.spa : 'ARP reply: ' + d.arp.spa + ' is at ' + d.arp.sha;
  return 'ICMP ' + (d.icmp.type === 'echo_req' ? 'echo request ' : 'echo reply ') + d.ip.src + ' → ' + d.ip.dst;
}

const ownerOf = (topo, mac, ip) => {
  for (const n of topo.nodes) for (const f of n.ifaces) if (f.mac === mac && f.ip !== ip) return n.id;
  return null;
};

function ifaceLanName(topo, nodeId, idx) {
  const n = topo.nodes.find((x) => x.id === nodeId);
  const f = n && n.ifaces[idx];
  return f ? topo.lans[f.lan].name : '';
}

/* ---- node ---- */
export function nodePanel(ctx, id) {
  const { topo, events, t, mode, errors } = ctx;
  const n = topo.nodes.find((x) => x.id === id);
  if (!n) return '';
  const fo = foldAt(events, t);
  const cache = fo.caches[id] || {};
  const err = (key) => errors.find((e) => e.key === key);
  const isEdit = mode === 'edit';
  let h = `<div class="panel-h"><span class="lbl">${n.kind}</span><h2 class="mono">${esc(id)}</h2>
    <span class="chip ${isEdit ? 'mute' : 'green'}" style="margin-left:auto">${isEdit ? 'not joined' : 'joined'}</span></div>`;
  if (isEdit) {
    h += `<div class="sec fields"><span class="lbl">Edit node</span>`;
    n.ifaces.forEach((f) => {
      const k = id + ':' + f.idx + ':ip', e = err(k);
      h += `<label class="field"><span class="muted">if${f.idx} · IP address</span>
        <input class="mono ${e ? 'err' : ''}" value="${esc(f.ip)}" data-edit="${esc(id)}|${f.idx}|ip" aria-invalid="${!!e}">
        ${e ? `<span class="em">✕ ${esc(e.msg)} (${esc(e.path)})</span>` : ''}</label>
        <label class="field"><span class="muted">if${f.idx} · MAC (auto-assigned, editable)</span>
        <input class="mono" value="${esc(f.mac)}" data-edit="${esc(id)}|${f.idx}|mac" maxlength="17"></label>`;
    });
    h += `</div>`;
  }
  h += `<div class="sec"><span class="lbl">Interfaces</span><table class="tb"><thead><tr><th>iface</th><th>LAN</th><th>IP / prefix <span class="muted">(logical)</span></th><th>MAC <span class="muted">(physical)</span></th></tr></thead><tbody>`;
  n.ifaces.forEach((f) => {
    h += `<tr><td class="mono">if${f.idx}</td><td>${esc(topo.lans[f.lan].name)}</td><td class="mono">${esc(f.ip)}/${f.prefix}</td><td class="mono">${esc(f.mac)}</td></tr>`;
  });
  h += `</tbody></table></div>`;
  const rows = Object.keys(cache).map((ip) => {
    const e = cache[ip];
    const owner = e.mac ? ownerOf(topo, e.mac, ip) : null;
    const hint = owner && n.kind !== 'router' ? `<div style="color:var(--violet);font-size:11px;margin-top:2px">MAC belongs to ${esc(owner)} (proxy)</div>` : '';
    const left = e.ttl ? Math.max(0, e.ttl - (t - e.t0)) : 0;
    const pending = e.state === 'PENDING';
    return `<tr><td class="mono">${esc(ip)}</td><td class="mono">${esc(e.mac || '—')}${hint}</td>
      <td><span class="chip ${pending ? 'amber' : 'green'}">${pending ? '<span class="spin"></span>' : ''}${e.state}</span>
      ${pending ? `<div class="mono muted" style="font-size:11px;margin-top:3px">try ${e.attempts} · queued ${e.queued}</div>` : ''}</td>
      <td>${pending ? '' : `<div class="vbar" title="${(left / 1000).toFixed(1)} s"><i style="width:${e.ttl ? Math.round((left / e.ttl) * 100) : 0}%"></i></div><div class="mono muted" style="font-size:11px">${(left / 1000).toFixed(1)} s</div>`}</td></tr>`;
  });
  h += `<div class="sec"><span class="lbl">ARP cache</span>`;
  h += rows.length
    ? `<table class="tb"><thead><tr><th>IP</th><th>MAC</th><th>state</th><th>TTL</th></tr></thead><tbody>${rows.join('')}</tbody></table>`
    : `<span class="muted">Empty at t = ${fmtS(t)} s. FREE entries are hidden.</span>`;
  h += `</div>`;
  const routes = n.ifaces.map((f) => `<tr><td class="mono">${esc(f.ip.split('.').slice(0, 3).join('.') + '.0/' + f.prefix)}</td><td>connected</td><td class="mono">if${f.idx}</td></tr>`);
  if (n.gateway) routes.push(`<tr><td class="mono">default</td><td class="mono">${esc(n.gateway)}</td><td class="mono">if0</td></tr>`);
  h += `<div class="sec"><span class="lbl">Routing table</span><table class="tb"><thead><tr><th>destination</th><th>via</th><th>iface</th></tr></thead><tbody>${routes.join('')}</tbody></table></div>`;
  if (n.kind === 'router') {
    const withProxy = n.ifaces.filter((f) => f.proxy && f.proxy.length);
    h += `<div class="sec"><span class="lbl">Proxy ARP list</span>` + (withProxy.map((f) => `<div style="margin:4px 0 2px" class="muted">if${f.idx} (${esc(topo.lans[f.lan].name)})</div><div style="display:flex;flex-wrap:wrap;gap:6px">${f.proxy.map((c) => `<span class="chip violet mono">${esc(c)}</span>`).join('')}</div>`).join('') || '<span class="muted">none</span>') +
      `<p class="muted" style="margin:8px 0 0;font-size:12px">${esc(n.id)} answers ARP requests for these addresses with its own MAC, so hosts on that LAN believe the far hosts are on their own wire.</p></div>`;
  }
  // every address in the topology except this node's own
  const targets = topo.nodes.filter((x) => x.id !== id).flatMap((x) => x.ifaces.map((f) => ({ ip: f.ip, label: `${f.ip}  ·  ${x.id}${x.ifaces.length > 1 ? ' (if' + f.idx + ')' : ''}` })));
  const pick = targets.some((t) => t.ip === ctx.pingDst) ? ctx.pingDst : (targets[0] || {}).ip;
  h += `<div class="sec fields"><span class="lbl">Actions</span>
    <div style="display:flex;gap:8px;align-items:end">
      <label class="field" style="flex:1"><span class="muted" style="font-size:12px">Ping destination</span>
        <select class="mono" id="ping-dst" aria-label="Ping destination">${targets.map((t) => `<option value="${esc(t.ip)}" ${t.ip === pick ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}</select></label>
      <label class="field" style="width:64px"><span class="muted" style="font-size:12px">Count</span>
        <select id="ping-count">${[1, 2, 3, 4, 5].map((c) => `<option ${c === (ctx.pingCount || 1) ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
    </div>
    <div style="display:flex;gap:8px"><button class="btn pri" style="flex:1;justify-content:center" data-act="ping" data-node="${esc(id)}">Ping…</button>
    <button class="btn" style="flex:1;justify-content:center" data-act="flush" data-node="${esc(id)}">Flush ARP cache</button></div></div>`;
  return h;
}

/* ---- LAN ---- */
export function lanPanel(ctx, lanId) {
  const { topo, events, t, scope } = ctx;
  const L = topo.lans[lanId];
  if (!L) return '';
  const fo = foldAt(events, t);
  const c = fo.counts[lanId] || { ok: 0, lost: 0, cor: 0 };
  const members = [];
  topo.nodes.forEach((n) => n.ifaces.forEach((f) => { if (f.lan === lanId) members.push(n.id + ':if' + f.idx); }));
  const macRows = Object.entries(fo.macs[lanId] || {});
  let h = `<div class="panel-h"><span class="lbl">${L.mode}</span><h2>${esc(L.name)}</h2><span class="chip mute" style="margin-left:auto">${esc(L.mode)}</span></div>`;
  h += `<div class="sec"><span class="lbl">Network</span><div class="mono">${esc(L.cidr)}</div>
    <p class="muted" style="margin:8px 0 0;font-size:12px">${L.mode === 'bus' ? 'A shared wire. Every frame reaches every station; stations whose MAC does not match mark it ignored.' : 'A learning switch. Broadcasts flood; known unicast goes to one port only.'}</p></div>`;
  h += `<div class="sec"><span class="lbl">Members</span><div style="display:flex;flex-wrap:wrap;gap:6px">${members.map((m) => `<span class="chip mute mono">${esc(m)}</span>`).join('')}</div></div>`;
  if (L.mode === 'star') {
    h += `<div class="sec"><span class="lbl">MAC table (switch_learn)</span>`;
    h += macRows.length ? `<table class="tb"><thead><tr><th>MAC</th><th>port (node:iface)</th></tr></thead><tbody>${macRows.map(([m, p]) => `<tr><td class="mono">${esc(m)}</td><td class="mono">${esc(p)}</td></tr>`).join('')}</tbody></table>`
      : `<span class="muted">Empty. The switch learns a MAC from the first frame each station sends. Ping h3 → h4 to watch it fill.</span>`;
    h += `</div>`;
  }
  h += `<div class="sec"><span class="lbl">Frames so far</span><div style="display:flex;gap:8px;flex-wrap:wrap">
    <span class="chip green">✓ ok ${c.ok}</span><span class="chip mute">✕ lost ${c.lost}</span><span class="chip red">≈ corrupted ${c.cor}</span></div></div>`;
  h += `<div class="sec" style="display:flex;align-items:center;gap:12px">
    <button class="sw" role="switch" aria-checked="${scope === String(lanId)}" aria-label="Override impairments for this LAN" data-act="override" data-lan="${lanId}"></button>
    <div><div style="font-weight:500">Override impairments for this LAN</div><div class="muted" style="font-size:12px">Sets the dock scope to ${esc(L.name)}.</div></div></div>`;
  return h;
}

/* ---- edge: frames on one line, newest first ---- */
export function edgePanel(ctx, edgeId) {
  const { topo, events, t } = ctx;
  const [nid, idx] = edgeId.split(':');
  const n = topo.nodes.find((x) => x.id === nid);
  if (!n) return '';
  const lan = n.ifaces[+idx].lan;
  const rows = events.filter((e) => e.ev === 'medium' && e.t <= t && e.lan === lan && (e.from === nid || e.to.some((r) => r.node === nid))).reverse();
  let h = `<div class="panel-h"><h2 class="mono">${esc(edgeId)}</h2><span class="muted">${esc(nid)} ⇄ ${esc(topo.lans[lan].name)}</span></div>`;
  h += `<div style="padding:12px 16px 4px"><span class="lbl">Frames on this line · newest first</span></div>`;
  if (!rows.length) h += `<div style="padding:8px 16px" class="muted">No frames yet at t = ${fmtS(t)} s.</div>`;
  rows.forEach((e) => {
    const tx = events.find((x) => x.ev === 'tx' && x.uid === e.uid);
    const k = kindOf(events, e.uid);
    const rx = events.find((x) => x.ev === 'rx' && x.uid === e.uid && x.node === nid && x.t <= t);
    const fate = e.from === nid ? (e.fate === 'ok' ? 'sent ✓' : e.fate) : rx ? rx.verdict : 'in flight';
    const chip = fate === 'accepted' || fate === 'sent ✓' ? 'green' : fate === 'ignored' || fate === 'in flight' ? 'mute' : 'red';
    h += `<button class="rowh" data-act="packet" data-uid="${esc(e.uid)}">
      <span class="mk ${k}" style="position:static;margin:0;width:16px;height:16px;font-size:9px;flex:none">${k === 'proxy' ? 'P' : ''}</span>
      <span style="flex:1;min-width:0"><span class="mono muted">${fmtS(e.t)} s</span><br>${esc(tx ? summaryOf(tx) : e.uid)}</span>
      <span class="chip ${chip}">${esc(fate)}</span></button>`;
  });
  return h;
}

/* ---- packet inspector ---- */
const BYTE_CELLS = {
  ethernet: [['type', 0, 1], ['src', 1, 6], ['dst', 7, 6], ['len', 13, 2], ['seq', 15, 2], ['payload', 17, 44], ['fcs', 61, 4]],
  arp: [['htype', 17, 2], ['ptype', 19, 2], ['hlen', 21, 1], ['plen', 22, 1], ['oper', 23, 2], ['sha', 25, 6], ['spa', 31, 4], ['tha', 35, 6], ['tpa', 41, 4]],
  ipv4: [['ver', 17, 1], ['tos', 18, 1], ['tlen', 19, 2], ['id', 21, 2], ['flags', 23, 2], ['ttl', 25, 1], ['proto', 26, 1], ['ick', 27, 2], ['isrc', 29, 4], ['idst', 33, 4]],
  icmp: [['ityp', 37, 1], ['icode', 38, 1], ['ick2', 39, 2], ['iid', 41, 2], ['iseq', 43, 2], ['idata', 45, 16]]
};

const cell = (name, val, off, len, extra) => `<div class="gc ${extra || ''}" data-off="${off}" data-len="${len}"><span class="n">${esc(name)}</span><span class="v">${esc(val)}</span></div>`;
const span = (w, html) => html.replace('class="gc', `style="grid-column:span ${w}" class="gc`);

export function packetPanel(ctx, uid) {
  const { events, t } = ctx;
  const tx = events.find((e) => e.ev === 'tx' && e.uid === uid);
  if (!tx) return `<div class="panel-h"><span class="muted">Unknown frame ${esc(uid)}</span></div>`;
  const d = tx.decoded, bytes = (tx.hex.match(/../g) || []).map((x) => parseInt(x, 16));
  const med = events.find((e) => e.ev === 'medium' && e.uid === uid);
  const fcsBad = med && med.fate === 'corrupted';
  const verdicts = events.filter((e) => e.ev === 'rx' && e.uid === uid && e.t <= t);
  const fcsHex = tx.hex.slice(122, 130).replace(/(..)(?=.)/g, '$1 ').trim();
  let h = `<div class="panel-h"><button class="btn sm" data-act="back">← Back</button><h2>Packet inspector</h2><span class="chip mono mute" style="margin-left:auto">${esc(uid)}</span></div>`;
  h += `<div class="sec"><span class="lbl">1 · Ethernet frame (lab1 order: src before dst)</span>
    <div style="display:grid;grid-template-columns:34px 1.6fr 1.6fr 28px 28px 1fr 1fr">
    ${span(1, cell('type', d.eth.type === 'ARP' ? '06' : '08', 0, 1, 'x'))}${cell('src MAC', d.eth.src, 1, 6, 'ty')}${cell('dst MAC', d.eth.dst, 7, 6, d.eth.dst === 'ff:ff:ff:ff:ff:ff' ? '' : 'tp')}${cell('len', String(d.eth.len), 13, 2)}${cell('seq', String(d.eth.seq), 15, 2)}${cell('payload', '44 B', 17, 44)}${cell('FCS ' + (fcsBad ? '✗' : '✓'), fcsHex, 61, 4)}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;align-items:center"><span class="lbl" style="margin:0">Receivers</span>
    ${verdicts.map((v) => `<span class="chip ${v.verdict === 'accepted' ? 'green' : v.verdict === 'ignored' ? 'mute' : 'red'} mono">${esc(v.node)}: ${v.verdict === 'accepted' ? '✓ accepted' : v.verdict === 'ignored' ? '– ignored (dst MAC not ours)' : '✗ FCS fail'}</span>`).join('') || '<span class="muted">none yet</span>'}</div></div>`;
  if (d.arp) {
    const a = d.arp;
    h += `<div class="sec"><span class="lbl">2 · ARP packet (32-bit layout)</span>
      <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr))"><div class="rul" style="grid-column:span 1">8 bits</div><div class="rul">8 bits</div><div class="rul" style="grid-column:span 2">16 bits</div></div>
      <div class="gridwrap" style="grid-template-columns:repeat(4,minmax(0,1fr))">
      ${span(2, cell('Hardware Type', a.htype + ' (Ethernet)', 17, 2))}${span(2, cell('Protocol Type', a.ptype + ' (IPv4)', 19, 2))}
      ${cell('Hardware length', String(a.hlen), 21, 1)}${cell('Protocol length', String(a.plen), 22, 1)}${span(2, cell('Operation', (a.oper === 'request' ? '1' : '2') + ' (' + a.oper + ')', 23, 2))}
      ${span(4, cell('Sender hardware address', a.sha, 25, 6, 'ty'))}${span(4, cell('Sender protocol address', a.spa, 31, 4))}
      ${span(4, cell('Target hardware address' + (a.oper === 'request' ? ' (all zeros in a request)' : ''), a.tha, 35, 6, 'tp'))}${span(4, cell('Target protocol address', a.tpa, 41, 4))}
      </div></div>`;
  } else {
    const p = d.ip, ic = d.icmp;
    h += `<div class="sec"><span class="lbl">2 · IPv4 header (20 B)</span>
      <div class="gridwrap" style="grid-template-columns:repeat(8,minmax(0,1fr))">
      ${span(1, cell('Version', '4', 17, 1))}${span(1, cell('IHL', '5', 17, 1))}${span(2, cell('TOS', '0', 18, 1))}${span(4, cell('Total length', '44', 19, 2))}
      ${span(4, cell('Identification', String(p.id), 21, 2))}${span(4, cell('Flags / fragment', '0', 23, 2))}${span(2, cell('TTL', String(p.ttl), 25, 1))}${span(2, cell('Protocol', p.proto + ' (ICMP)', 26, 1))}
      ${span(4, cell('Header checksum', '✓ verified', 27, 2))}${span(8, cell('Source IP', p.src, 29, 4))}${span(8, cell('Destination IP', p.dst, 33, 4))}
      </div></div>
      <div class="sec"><span class="lbl">ICMP echo</span>
      <div class="gridwrap" style="grid-template-columns:repeat(8,minmax(0,1fr));margin-top:6px">
      ${span(4, cell('Type', ic.type === 'echo_req' ? '8 (echo request)' : '0 (echo reply)', 37, 1))}${span(2, cell('Code', '0', 38, 1))}${span(2, cell('Checksum', '✓ verified', 39, 2))}
      ${span(4, cell('Identifier', String(ic.id), 41, 2))}${span(4, cell('Sequence', String(ic.seq), 43, 2))}${span(8, cell('Data (16 B)', 'ARPLAB-PING-' + ('000' + ic.seq).slice(-4), 45, 16))}
      </div></div>`;
  }
  h += `<div class="sec"><span class="lbl">3 · Hex dump · 65 bytes</span><div class="hexdump" id="hexdump">`;
  for (let off = 0; off < 65; off += 16) {
    h += `<div class="hexrow"><span class="mono muted" style="width:34px;flex:none">${off.toString(16).padStart(2, '0')}:</span>`;
    for (let i = off; i < Math.min(65, off + 16); i++) {
      const reg = i < 17 ? 'h' : i < 61 ? 'p' : 'f';
      h += `<span class="hx ${reg}" data-i="${i}">${bytes[i].toString(16).padStart(2, '0')}</span>`;
    }
    h += `</div>`;
  }
  h += `</div><div style="margin-top:8px;font-size:11.5px;color:var(--ink2);display:flex;gap:12px">
    <span><i class="swatch" style="background:var(--blue-bg)"></i> header 17 B</span><span><i class="swatch" style="background:var(--surface2)"></i> payload 44 B</span><span><i class="swatch" style="background:var(--amber-bg)"></i> FCS 4 B</span></div></div>`;
  const journey = events.filter((e) => e.uid === uid && e.t <= t);
  h += `<div class="sec"><span class="lbl">4 · Journey of ${esc(uid)}</span>`;
  journey.forEach((e) => {
    const dot = e.ev === 'tx' ? 'var(--ink)' : e.ev === 'medium' ? (e.fate === 'ok' ? 'var(--teal)' : 'var(--red)') : e.verdict === 'accepted' ? 'var(--green)' : e.verdict === 'ignored' ? 'var(--ink3)' : 'var(--red)';
    const text = e.ev === 'tx' ? esc(e.node) + '/if' + e.iface + ' transmits' : e.ev === 'medium' ? 'Enters LAN ' + 'AB'[e.lan] + ' · fate ' + esc(e.fate) + ' · delay ' + e.delay_ms + ' ms → ' + e.to.map((r) => esc(r.node)).join(', ') : esc(e.node) + ' ' + esc(e.verdict);
    h += `<div class="journey"><span class="mono muted">${fmtS(e.t)} s</span><i style="background:${dot}"></i><span>${text}</span></div>`;
  });
  return h + `</div>`;
}
