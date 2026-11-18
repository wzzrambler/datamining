const base = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/, '');

export function requestId() {
  if (crypto.randomUUID) return crypto.randomUUID();

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

export async function request(path, { method = 'GET', body, signal } = {}) {
  const response = await fetch(base + path, {
    method, signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const error = new Error(result.error?.message || `服务返回 ${response.status}`);
    error.code = result.error?.code;
    throw error;
  }
  return response;
}

export async function json(path, options) {
  return (await request(path, options)).json();
}

export function params(values) {
  const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value != null && value !== ''));
  return query.size ? '?' + query : '';
}

export async function readEvents(response, handle) {
  if (!response.body) throw new Error('浏览器未能读取事件流');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let split = buffer.indexOf('\n\n');
      while (split >= 0) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const fields = Object.fromEntries(frame.split('\n').filter(line => line.includes(': '))
          .map(line => [line.slice(0, line.indexOf(': ')), line.slice(line.indexOf(': ') + 2)]));
        if (fields.event && fields.data) await handle(fields.event, JSON.parse(fields.data), Number(fields.id || 0));
        split = buffer.indexOf('\n\n');
      }
    }
  } finally {
    reader.releaseLock();
  }
}
