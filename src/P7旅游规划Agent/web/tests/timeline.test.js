import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildTimeline, mergeActivityEvents } from '../src/timeline.js';

const message = (turn_id, role) => ({ turn_id, role, message_id: turn_id + role });
const event = (turn_id, seq, payload) => ({ turn_id, seq, payload });

test('places older and live tools in their own turns and merges start with result', () => {
  const messages = [message('a', 'user'), message('a', 'assistant'), message('b', 'user')];
  const legacy = event('a', 2, { title: '旧查询', stage: 'ok' });
  const start = event('b', 2, { activity_id: 'x', title: '查景点' });
  const ordered = buildTimeline(messages, [start, legacy]);
  assert.deepEqual(ordered.map(row => row.title || row.message_id),
    ['auser', '旧查询', 'aassistant', 'buser', '查景点']);
  assert.equal(ordered.at(-1).stage, 'running');

  const result = event('b', 4, { activity_id: 'x', stage: 'ok', result_count: 2 });
  const replay = mergeActivityEvents([legacy, start, result], [start, result]);
  const complete = buildTimeline([...messages, message('b', 'assistant')], replay);
  assert.deepEqual(complete.map(row => row.title || row.message_id),
    ['auser', '旧查询', 'aassistant', 'buser', '查景点', 'bassistant']);
  assert.equal(complete[4].result_count, 2);
});

test('keeps tool cards before a reply when older pages contain its user message', () => {
  const result = event('a', 9, { title: '查看来源', stage: 'no_data' });
  const final = message('a', 'assistant');
  assert.deepEqual(buildTimeline([final], [result]).map(row => row.title || row.message_id),
    ['查看来源', 'aassistant']);
  const merged = mergeActivityEvents([result], [result]);
  assert.deepEqual(buildTimeline([message('a', 'user'), final], merged).map(row => row.title || row.message_id),
    ['auser', '查看来源', 'aassistant']);
});
