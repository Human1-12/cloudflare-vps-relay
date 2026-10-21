import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHeader, uuidBytes, checkTarget, joinBytes, MAX_BUFFER } from '../src/protocol.mjs';

const id = '00112233-4455-6677-8899-aabbccddeeff'; // Test fixture, never deployed.
function packet(host = 'vps.example.com', port = 50001, command = 1) {
  const name = new TextEncoder().encode(host);
  return Uint8Array.from([0, ...uuidBytes(id), 0, command, port >> 8, port & 255, 2, name.length, ...name, 7, 8, 9]);
}

test('fragmented headers remain incomplete until their destination is present', () => {
  const full = packet();
  const size = parseHeader(full).offset;
  for (let n = 0; n < size; n++) assert.equal(parseHeader(full.slice(0, n)), null);
  const result = parseHeader(full);
  assert.equal(result.host, 'vps.example.com');
  assert.equal(result.port, 50001);
  assert.deepEqual(Array.from(full.slice(result.offset)), [7, 8, 9]);
});
test('a different target or port is rejected rather than forwarded', () => {
  const result = parseHeader(packet());
  assert.throws(() => checkTarget(result, 'other.example.com', 50001));
  assert.throws(() => checkTarget(result, 'vps.example.com', 443));
  checkTarget(result, 'VPS.EXAMPLE.COM.', 50001);
});
test('UDP, MUX, unsupported version and invalid address types are rejected', () => {
  assert.throws(() => parseHeader(packet('vps.example.com', 50001, 2)));
  assert.throws(() => parseHeader(packet('vps.example.com', 50001, 3)));
  const version = packet(); version[0] = 1;
  assert.throws(() => parseHeader(version));
  const type = packet(); type[21] = 9;
  assert.throws(() => parseHeader(type));
});
test('UUID validation is strict and buffers have a hard upper bound', () => {
  assert.throws(() => uuidBytes('not-a-uuid'));
  assert.equal(uuidBytes(id).length, 16);
  assert.throws(() => joinBytes(new Uint8Array(MAX_BUFFER), new Uint8Array(1)));
});
test('IPv4 and IPv6 destinations are parsed with exact bounds', () => {
  const prefix = [0, ...uuidBytes(id), 0, 1, 1, 187];
  const ipv4 = Uint8Array.from([...prefix, 1, 203, 0, 113, 3]);
  assert.equal(parseHeader(ipv4).host, '203.0.113.3');
  assert.equal(parseHeader(ipv4.slice(0, -1)), null);
  const ipv6 = Uint8Array.from([...prefix, 3, ...Array(16).fill(0)]);
  assert.equal(parseHeader(ipv6).host, '0:0:0:0:0:0:0:0');
});
