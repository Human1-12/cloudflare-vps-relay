// Parse a bounded VLESS TCP request. The only permitted destination is one VPS.
export const MAX_HEADER = 1024;
export const MAX_BUFFER = 4 * 1024 * 1024;

export function uuidBytes(value) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || '')) {
    throw new Error('Invalid relay credential');
  }
  return Uint8Array.from(value.replaceAll('-', '').match(/../g), s => parseInt(s, 16));
}

export function normalizeHost(host) {
  return String(host).toLowerCase().replace(/\.$/, '');
}

export function parseHeader(data) {
  if (data.length < 18) return null;
  if (data[0] !== 0) throw new Error('Unsupported version');
  let offset = 18 + data[17];
  if (offset + 4 > MAX_HEADER) throw new Error('Header too large');
  if (data.length < offset + 4) return null;
  const command = data[offset++];
  if (command !== 1) throw new Error('TCP only');
  const port = (data[offset++] << 8) | data[offset++];
  const type = data[offset++];
  let host;
  if (type === 1) {
    if (data.length < offset + 4) return null;
    host = Array.from(data.subarray(offset, offset + 4)).join('.');
    offset += 4;
  } else if (type === 2) {
    if (data.length < offset + 1) return null;
    const size = data[offset++];
    if (!size) throw new Error('Empty hostname');
    if (data.length < offset + size) return null;
    host = new TextDecoder('utf-8', { fatal: true }).decode(data.subarray(offset, offset + size));
    if (!/^[a-zA-Z0-9.-]+$/.test(host)) throw new Error('Invalid hostname');
    offset += size;
  } else if (type === 3) {
    if (data.length < offset + 16) return null;
    const groups = [];
    for (let i = 0; i < 16; i += 2) groups.push(((data[offset + i] << 8) | data[offset + i + 1]).toString(16));
    host = groups.join(':');
    offset += 16;
  } else {
    throw new Error('Invalid address type');
  }
  return { version: data[0], credential: data.slice(1, 17), host: normalizeHost(host), port, offset };
}

export function checkTarget(header, originHost, originPort) {
  // Deliberately disallow arbitrary destinations and any proxy/route fallback.
  if (header.host !== normalizeHost(originHost) || header.port !== Number(originPort)) {
    throw new Error('Destination not permitted');
  }
}

export function joinBytes(a, b) {
  if (a.length + b.length > MAX_BUFFER) throw new Error('Buffer limit');
  const result = new Uint8Array(a.length + b.length);
  result.set(a);
  result.set(b, a.length);
  return result;
}
