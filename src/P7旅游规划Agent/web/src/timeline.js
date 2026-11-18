export function mergeActivityEvents(previous, incoming) {
  const unique = new Map();
  for (const entry of [...previous, ...incoming]) {
    unique.set(`${entry.turn_id}:${entry.seq}`, entry);
  }
  return [...unique.values()];
}

function toolCards(events) {
  const indexed = new Map();
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    const data = event.payload;
    const key = data.activity_id || `${event.turn_id}:${event.seq}`;
    const existing = indexed.get(key);
    indexed.set(key, {
      ...existing, ...data, key: `tool:${key}`,
      kind: 'activity',
      stage: data.stage || existing?.stage || 'running',
    });
  }
  return [...indexed.values()];
}

export function buildTimeline(messages, activities) {
  const grouped = new Map();
  for (const event of activities) {
    if (!grouped.has(event.turn_id)) grouped.set(event.turn_id, []);
    grouped.get(event.turn_id).push(event);
  }

  const answered = new Set(messages.filter(m => m.role === 'assistant').map(m => m.turn_id));
  const rendered = new Set();
  const timeline = [];
  function placeTools(turn) {
    if (!turn || rendered.has(turn)) return;
    rendered.add(turn);
    timeline.push(...toolCards(grouped.get(turn) || []));
  }

  for (const message of messages) {
    if (message.role === 'assistant') placeTools(message.turn_id);
    timeline.push({ ...message, kind: 'message', key: `message:${message.message_id}` });
    if (message.role === 'user' && !answered.has(message.turn_id)) placeTools(message.turn_id);
  }
  return timeline;
}
