// The only file that touches WebSocket. Exposes connect/send/onMessage/onStatus (contract §3).
const msgHandlers = [];
const statusHandlers = [];
let socket = null;
let url = null;
let attempt = 0;
let retryTimer = null;

function emitStatus(state, info) {
  statusHandlers.forEach((h) => h(state, info));
}

function open() {
  try {
    socket = new WebSocket(url);
  } catch (err) {
    scheduleRetry(String(err));
    return;
  }
  socket.onopen = () => {
    attempt = 0;
    emitStatus('connected', { attempt: 0 });
  };
  socket.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (err) {
      return; // ignore non-JSON frames
    }
    msgHandlers.forEach((h) => h(msg));
  };
  socket.onclose = () => scheduleRetry('closed');
  socket.onerror = () => {}; // onclose follows and handles retry
}

// Backoff 1 s, 2 s, 4 s, ... capped at 8 s (design banner shows 1 -> 2 -> 4 s).
function scheduleRetry(reason) {
  attempt += 1;
  const delay = Math.min(8000, 1000 * Math.pow(2, attempt - 1));
  emitStatus('disconnected', { attempt, delay, reason });
  clearTimeout(retryTimer);
  retryTimer = setTimeout(open, delay);
}

export function connect(wsUrl) {
  url = wsUrl;
  clearTimeout(retryTimer);
  if (socket) socket.close();
  emitStatus('connecting', { attempt });
  open();
}

export function send(cmdObject) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(cmdObject));
    return true;
  }
  return false;
}

export function onMessage(handler) {
  msgHandlers.push(handler);
}

export function onStatus(handler) {
  statusHandlers.push(handler);
}
