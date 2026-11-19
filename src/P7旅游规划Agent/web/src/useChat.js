import { nextTick, reactive, ref } from 'vue';
import { json, params, readEvents, request, requestId } from './api';
import { mergeActivityEvents } from './timeline';

export function useChat() {
  const conversations = ref([]);
  const nextConversation = ref(null);
  const current = ref(null);
  const messages = ref([]);
  const activities = ref([]);
  const before = ref(null);
  const plan = ref(null);
  const drafts = reactive({});
  const runs = reactive({});
  const error = ref('');
  const status = ref(null);
  let selection = 0;
  const streams = new Map();

  function record(cid) {
    return (runs[cid] ||= { turn: null, last: 0, activities: [], running: false, stopping: false, disconnected: false, delta: '' });
  }

  async function list(more = false) {
    const data = await json('/conversations' + params({ cursor: more ? nextConversation.value : null }));
    conversations.value = more ? [...conversations.value, ...data.items] : data.items;
    nextConversation.value = data.next_cursor;
  }

  async function refresh(cid) {
    const [conv, history] = await Promise.all([json(`/conversations/${cid}`), json(`/conversations/${cid}/messages`)]);
    if (current.value?.id !== cid) return;
    current.value = conv;
    messages.value = history.items;
    activities.value = history.activities || [];
    before.value = history.next_before_seq;
    if (conv.active_itinerary) await showPlan(conv.active_itinerary.itinerary_id, conv.active_itinerary.version, cid);
    const state = record(cid);
    if (conv.active_turn && !streams.has(cid)) {
      if (state.turn !== conv.active_turn.id) Object.assign(state, { turn: conv.active_turn.id, last: 0, activities: [], delta: '' });
      consume(cid);
    }
  }

  async function choose(cid) {
    const nonce = ++selection;
    error.value = '';
    messages.value = [];
    activities.value = [];
    plan.value = null;
    before.value = null;
    current.value = cid ? { id: cid } : null;
    if (cid) {
      window.history.replaceState(null, '', `?chat=${cid}`);
      await refresh(cid);
      if (nonce !== selection) return;
    } else {
      window.history.replaceState(null, '', window.location.pathname);
    }
  }

  async function boot() {
    try {
      [status.value] = await Promise.all([json('/status'), list()]);
      const id = new URLSearchParams(window.location.search).get('chat');
      if (id) await choose(id);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function showPlan(id, version, cid = current.value?.id) {
    const result = await json(`/itineraries/${id}` + params({ version }));
    if (current.value?.id === cid) plan.value = result;
    return result;
  }

  async function consume(cid, body) {
    if (streams.has(cid)) return;
    const state = record(cid);
    const control = new AbortController();
    streams.set(cid, control);
    state.running = true;
    state.disconnected = false;
    let ended = false;
    try {
      const path = body ? `/conversations/${cid}/turns` : `/turns/${state.turn}/events` + params({ after: state.last });
      const response = await request(path, { method: body ? 'POST' : 'GET', body, signal: control.signal });
      await readEvents(response, async (event, data, seq) => {
        if (seq && seq <= state.last) return;
        state.last = seq || state.last;
        if (event === 'turn.started') {
          state.turn = data.turn_id;
          if (current.value?.id === cid && state.request) {
            const optimistic = messages.value.find(m => m.message_id === state.request.client_message_id);
            if (optimistic) {
              optimistic.turn_id = data.turn_id;
              optimistic.data_version = data.data_version;
            }
          }
        }
        if (event === 'tool.started' || event === 'tool.progress') {
          state.delta = '';
          state.activities.push({ turn_id: state.turn, seq, event_type: event, payload: data });
        }
        if (event === 'message.delta') {
          if (state.draftMessage !== data.message_id) {
            state.draftMessage = data.message_id;
            state.delta = '';
          }
          state.delta += data.text_delta;
        }
        if (event === 'message.final' && current.value?.id === cid) {
          state.delta = '';
          const entry = { ...data, turn_id: data.turn_id || state.turn, role: 'assistant' };
          if (!messages.value.some(m => m.message_id === entry.message_id)) messages.value.push(entry);
        }
        if (event === 'itinerary.changed' && current.value?.id === cid) {
          showPlan(data.itinerary_id, data.version, cid).catch(e => { error.value = e.message; });
        }
        if (event === 'turn.ended') {
          ended = true;
          state.running = false;
          state.stopping = false;
          state.request = null;
          if (data.error && current.value?.id === cid) error.value = data.error.message;
        }
      });
      if (!ended) {
        const conv = await json(`/conversations/${cid}`);
        ended = !conv.active_turn;
      }
      if (ended && current.value?.id === cid) await refresh(cid);
      if (ended) await list();
    } catch (e) {
      if (current.value?.id === cid) error.value = e.message;
      if (e.code) {
        ended = true;
        state.request = null;
        if (body) {
          drafts[cid] = body.text;
          if (current.value?.id === cid) await refresh(cid).catch(() => {});
        }
      }
    } finally {
      streams.delete(cid);
      state.running = false;
      state.disconnected = !ended;
    }
  }

  async function send(text, { replyTo, target } = {}) {
    text = text.trim();
    if (!text) return;
    let cid = current.value?.id;
    try {
      if (!cid) {
        const created = await json('/conversations', { method: 'POST', body: {} });
        cid = created.id;
        await choose(cid);
        drafts.start = '';
      }
      if (streams.has(cid)) return;
      const state = record(cid);
      if (state.disconnected) return;
      const body = { client_message_id: requestId(), text,
        reply_to: replyTo || current.value?.pending_question?.id || undefined,
        target_ref: target };
      Object.assign(state, { turn: null, last: 0, activities: [], request: body, delta: '' });
      messages.value.push({ message_id: body.client_message_id, role: 'user', markdown: text, attachments: [] });
      drafts[cid] = '';
      error.value = '';
      await nextTick();
      consume(cid, body);
    } catch (e) {
      error.value = e.message;
    }
  }

  async function stop() {
    const cid = current.value?.id;
    const state = cid && record(cid);
    if (!state?.turn) return;
    try {
      state.stopping = true;
      await json(`/turns/${state.turn}/cancel`, { method: 'POST' });
    } catch (e) {
      state.stopping = false;
      error.value = e.message;
    }
  }

  async function older() {
    const cid = current.value?.id;
    if (!cid || before.value == null) return;
    const data = await json(`/conversations/${cid}/messages` + params({ before_seq: before.value }));
    if (current.value?.id !== cid) return;
    messages.value = [...data.items, ...messages.value];
    activities.value = mergeActivityEvents(activities.value, data.activities || []);
    before.value = data.next_before_seq;
  }

  return { conversations, nextConversation, current, messages, activities, before, plan, drafts, runs, error, status,
    boot, list, choose, refresh, showPlan, send, stop, older, reconnect: () => {
      const cid = current.value?.id;
      if (!cid) return;
      const state = record(cid);
      return consume(cid, state.turn ? undefined : state.request);
    } };
}
