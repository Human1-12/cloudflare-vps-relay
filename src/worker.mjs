import { connect } from 'cloudflare:sockets';
import { parseHeader, uuidBytes, checkTarget, joinBytes, MAX_HEADER, MAX_BUFFER } from './protocol.mjs';

const OPEN_TIMEOUT_MS = 10000;
const AUTH_TIMEOUT_MS = 10000;

async function probeResponse(request, env, url) {
  try {
    if (!crypto.subtle.timingSafeEqual(uuidBytes(request.headers.get('X-Relay-Probe')), uuidBytes(env.RELAY_UUID))) throw new Error();
  } catch { return new Response('Unauthorized', { status: 401 }); }
  const seconds = Number(url.searchParams.get('seconds') || '120');
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 300) return new Response('Seconds must be 1-300', { status: 400 });
  let timer;
  let cancelled = false;
  const start = Date.now();
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      const tick = () => {
        if (cancelled) return;
        const elapsed = Date.now() - start;
        const done = elapsed >= seconds * 1000;
        controller.enqueue(encoder.encode(JSON.stringify({ elapsed_ms: elapsed, done }) + '\n'));
        if (done) controller.close();
        else timer = setTimeout(tick, Math.min(5000, seconds * 1000 - elapsed));
      };
      tick();
    },
    cancel() { cancelled = true; clearTimeout(timer); }
  });
  return new Response(body, { headers: {
    'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store, no-transform',
    'X-Content-Type-Options': 'nosniff'
  } });
}

function closeWebSocket(ws, code = 1000) {
  try { ws.close(code, code === 1000 ? 'closed' : 'relay unavailable'); } catch {}
}

async function relay(ws, env) {
  let socket;
  let writer;
  let reader;
  let queueBytes = 0;
  let streamController;
  let ended = false;
  const endInput = error => {
    if (ended) return;
    ended = true;
    if (error) streamController.error(error); else streamController.close();
  };
  const input = new ReadableStream({
    start(controller) { streamController = controller; },
    cancel() { ended = true; }
  });
  ws.addEventListener('message', event => {
    if (ended) return;
    if (!(event.data instanceof ArrayBuffer)) {
      endInput(new Error('Binary messages required'));
      return;
    }
    queueBytes += event.data.byteLength;
    if (queueBytes > MAX_BUFFER) { endInput(new Error('Buffer limit')); return; }
    streamController.enqueue(new Uint8Array(event.data));
  });
  ws.addEventListener('close', () => endInput());
  ws.addEventListener('error', () => endInput(new Error('WebSocket error')));
  let authTimer;
  let openTimer;
  let authenticated = false;
  try {
    reader = input.getReader();
    authTimer = setTimeout(() => endInput(new Error('Authentication timeout')), AUTH_TIMEOUT_MS);
    let buffer = new Uint8Array();
    let header;
    while (!header) {
      const next = await reader.read();
      if (next.done) throw new Error('Incomplete request');
      queueBytes -= next.value.length;
      buffer = joinBytes(buffer, next.value);
      header = parseHeader(buffer);
      if (!header && buffer.length > MAX_HEADER) throw new Error('Header too large');
    }
    const expected = uuidBytes(env.RELAY_UUID);
    if (!crypto.subtle.timingSafeEqual(header.credential, expected)) throw new Error('Unauthorized');
    checkTarget(header, env.ORIGIN_HOST, env.ORIGIN_PORT);
    authenticated = true;
    clearTimeout(authTimer);
    // A single fixed destination. No public ProxyIP lists, random exits or retries.
    socket = connect({ hostname: env.ORIGIN_HOST, port: Number(env.ORIGIN_PORT) });
    await Promise.race([
      socket.opened,
      new Promise((_, reject) => { openTimer = setTimeout(() => reject(new Error('Upstream timeout')), OPEN_TIMEOUT_MS); })
    ]);
    clearTimeout(openTimer);
    writer = socket.writable.getWriter();
    // VLESS response header belongs to the outer layer only.
    ws.send(new Uint8Array([header.version, 0]));
    const payload = buffer.subarray(header.offset);
    if (payload.length) await writer.write(payload);
    buffer = null;
    const upload = (async () => {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        queueBytes -= next.value.length;
        await writer.write(next.value);
      }
      await writer.close();
    })();
    const download = socket.readable.pipeTo(new WritableStream({
      write(chunk) { ws.send(chunk); }
    }));
    // Either side closing ends this connection; never reconnect to a different exit.
    await Promise.race([upload, download]);
    closeWebSocket(ws);
    endInput();
    try { await socket.close(); } catch {}
    await Promise.allSettled([upload, download]);
  } catch {
    // Do not log credentials, hostnames, request paths or inner traffic.
    if (authenticated) console.error(JSON.stringify({ event: 'relay_connection_ended' }));
    closeWebSocket(ws, authenticated ? 1011 : 1008);
  } finally {
    clearTimeout(authTimer);
    clearTimeout(openTimer);
    endInput();
    try { await reader?.cancel(); } catch {}
    try { await socket?.close(); } catch {}
    try { writer?.releaseLock(); } catch {}
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/health' && !request.headers.has('Upgrade')) {
      return Response.json({ service: 'fixed-vps-relay', version: '0.1.0' }, { headers: { 'Cache-Control': 'no-store' } });
    }
    if (request.method === 'GET' && url.pathname === '/probe' && !request.headers.has('Upgrade')) {
      return probeResponse(request, env, url);
    }
    if (url.pathname !== '/relay' || request.method !== 'GET') return new Response('Not found', { status: 404 });
    if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return new Response('WebSocket required', { status: 426 });
    // Early data stays disabled, reducing parsing complexity and secret-in-header logging.
    if (request.headers.has('Sec-WebSocket-Protocol')) return new Response('Early data disabled', { status: 400 });
    try {
      uuidBytes(env.RELAY_UUID);
      if (!env.ORIGIN_HOST || !Number.isInteger(Number(env.ORIGIN_PORT)) || Number(env.ORIGIN_PORT) < 1 || Number(env.ORIGIN_PORT) > 65535) throw new Error();
    } catch { return new Response('Service unavailable', { status: 503 }); }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.binaryType = 'arraybuffer';
    server.accept();
    ctx.waitUntil(relay(server, env));
    return new Response(null, { status: 101, webSocket: client });
  }
};
