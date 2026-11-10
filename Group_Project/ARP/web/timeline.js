// Timeline helpers: density histogram, step targets, and .jsonl export/import (one event per line).

const BUCKETS = 20;

// Counts events per time bucket, coloured by ARP / IP / errors (the design's histogram).
export function histogram(events, endT) {
  const b = Array.from({ length: BUCKETS }, () => ({ arp: 0, ip: 0, err: 0 }));
  const span = Math.max(1, endT / BUCKETS);
  events.forEach((e) => {
    if (e.ev === 'ready') return;
    const i = Math.min(BUCKETS - 1, Math.floor(e.t / span));
    if ((e.ev === 'medium' && e.fate !== 'ok') || (e.ev === 'rx' && e.verdict === 'fcs_fail')) b[i].err++;
    else if (e.ev === 'ip' || (e.ev === 'tx' && e.decoded && e.decoded.ip)) b[i].ip++;
    else b[i].arp++;
  });
  const max = Math.max(1, ...b.map((x) => x.arp + x.ip + x.err));
  return b.map((x) => ({ arp: (x.arp / max) * 28, ip: (x.ip / max) * 28, err: (x.err / max) * 28 }));
}

// Sorted distinct event times, used by the previous/next buttons.
export function eventTimes(events) {
  return [...new Set(events.map((e) => e.t))].sort((a, b) => a - b);
}

export function nextTime(times, cur) {
  return times.find((x) => x > cur + 0.5);
}

export function prevTime(times, cur) {
  return times.slice().reverse().find((x) => x < cur - 0.5);
}

export function toJsonl(events) {
  return events.map((e) => JSON.stringify(e)).join('\n') + '\n';
}

// Parses .jsonl text. Throws with the line number on a bad line, so the caller can show it.
export function fromJsonl(text) {
  return text.split(/\r?\n/).filter((l) => l.trim()).map((line, i) => {
    let e;
    try {
      e = JSON.parse(line);
    } catch (err) {
      throw new Error('line ' + (i + 1) + ' is not JSON');
    }
    if (typeof e.ev !== 'string' || typeof e.t !== 'number') throw new Error('line ' + (i + 1) + ' has no ev/t');
    return e;
  });
}
