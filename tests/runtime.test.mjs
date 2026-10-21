import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { uuidBytes } from '../src/protocol.mjs';

const credential = '00112233-4455-6677-8899-aabbccddeeff';

test('real Workers runtime: HTTP, authentication, destination restrictions and TCP echo', async () => {
  let connections = 0;
  const echo = net.createServer(socket => {
    connections++;
    socket.on('error', () => {}); // A peer reset during runtime disposal is normal; payload assertions remain required.
    socket.pipe(socket);
  });
  await new Promise(resolve => echo.listen(0, '127.0.0.1', resolve));
  const port = echo.address().port;
  let mf;
  try {
    mf = new Miniflare(convertV4MiniflareOptions({
      modules: ['worker.mjs', 'protocol.mjs'].map(name => ({
        type: 'ESModule', path: fileURLToPath(new URL('../src/' + name, import.meta.url))
      })),
      cf: false,
      compatibilityDate: '2026-10-21',
      bindings: { RELAY_UUID: credential, ORIGIN_HOST: '127.0.0.1', ORIGIN_PORT: String(port) }
    }));
    assert.equal((await mf.dispatchFetch('https://relay.example.com/health')).status, 200);
    assert.equal((await mf.dispatchFetch('https://relay.example.com/relay')).status, 426);
    assert.equal((await mf.dispatchFetch('https://relay.example.com/anything')).status, 404);
    assert.equal((await mf.dispatchFetch('https://relay.example.com/probe')).status, 401);
    const heartbeat = await mf.dispatchFetch('https://relay.example.com/probe?seconds=1', {
      headers: { 'X-Relay-Probe': credential }
    });
    const heartbeatEvents = (await heartbeat.text()).trim().split('\n').map(line => JSON.parse(line));
    assert.equal(heartbeatEvents[0].done, false);
    assert.equal(heartbeatEvents.at(-1).done, true);
    assert.ok(heartbeatEvents.at(-1).elapsed_ms >= 1000);
    assert.equal((await mf.dispatchFetch('https://relay.example.com/relay', {
      headers: { Upgrade: 'websocket', 'Sec-WebSocket-Protocol': 'early-data' }
    })).status, 400);
    const makePacket = (badId = false, badPort = false) => {
      const id = uuidBytes(credential);
      if (badId) id[0] ^= 1;
      const target = badPort ? (port === 65535 ? port - 1 : port + 1) : port;
      return Uint8Array.from([0, ...id, 0, 1, target >> 8, target & 255, 1, 127, 0, 0, 1]);
    };
    for (const [badId, badPort] of [[true, false], [false, true]]) {
      const result = await mf.dispatchFetch('https://relay.example.com/relay', { headers: { Upgrade: 'websocket' } });
      const ws = result.webSocket;
      ws.binaryType = 'arraybuffer';
      ws.accept();
      const closed = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Rejected client did not close')), 5000);
        ws.addEventListener('close', event => { clearTimeout(timer); resolve(event.code); });
      });
      ws.send(makePacket(badId, badPort));
      assert.equal(await closed, 1008);
      assert.equal(connections, 0, 'unauthorized requests must not open a TCP socket');
    }
    const result = await mf.dispatchFetch('https://relay.example.com/relay', { headers: { Upgrade: 'websocket' } });
    const ws = result.webSocket;
    ws.binaryType = 'arraybuffer';
    ws.accept();
    const received = [];
    const finished = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('No TCP echo')), 10000);
      ws.addEventListener('message', event => {
        received.push(...new Uint8Array(event.data));
        if (received.length >= 7) { clearTimeout(timer); resolve(); }
      });
    });
    const header = makePacket();
    ws.send(header.slice(0, 9));
    ws.send(Uint8Array.from([...header.slice(9), 1, 2, 3, 4, 5]));
    await finished;
    assert.deepEqual(received, [0, 0, 1, 2, 3, 4, 5]);
    assert.equal(connections, 1);
    ws.close();
  } finally {
    await mf?.dispose();
    await new Promise(resolve => echo.close(resolve));
  }
});
