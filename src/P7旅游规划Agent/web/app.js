/* 行迹 · 前端逻辑
   只用浏览器原生能力：EventSource 逐轮接收，fetch 兜底，无框架无构建。 */

const $ = (sel) => document.querySelector(sel);

/* 离线单页（tools/P7/build_offline.py 生成）把七条真实结果内联进 HTML，
   此时不需要后端；纯静态托管时页面从同目录 offline.json 取同一份数据；
   直接双击源文件没有数据，只给引导，不假装能规划。 */
let offlineData = window.__P7_OFFLINE__ || null;
const IS_FILE = window.location.protocol === 'file:';

const STATUS_TEXT = {
  ok: '成行',
  partial: '成行 · 有提醒',
  need_info: '需要补充',
  no_data: '库内无数据',
  conflict: '条件冲突',
};

const STATUS_TITLE = {
  need_info: '还差一个目的地',
  no_data: '库里没有这个景区',
  conflict: '这两个景区一天排不开',
};

const FALLBACK_EXAMPLES = [
  '带爸妈去九寨沟玩一天，他们走不动太久',
  '带6岁孩子去广州长隆，一天怎么安排',
  '和女朋友去珠海长隆海洋王国玩两天',
];

const state = {
  mode: localStorage.getItem('p7.mode') || 'rule',
  llmReady: false,
  running: false,
  offline: false,
  source: null,
  result: null,
  startedAt: 0,
};

/* ---------- 基础工具 ---------- */

function authHeaders() {
  const token = localStorage.getItem('p7.token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function withToken(url) {
  const token = localStorage.getItem('p7.token');
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

async function ensureAuth() {
  const response = await fetch('/api/health', { headers: authHeaders() });
  if (response.status !== 401) { refreshLockBtn(); return true; }
  const token = window.prompt('This service requires an access token (P7_ACCESS_TOKEN):');
  if (!token) { refreshLockBtn(); return false; }
  localStorage.setItem('p7.token', token.trim());
  const ok = (await fetch('/api/health', { headers: authHeaders() })).ok;
  refreshLockBtn();
  if (!ok) showError('Access token verification failed, please try again.');
  return ok;
}

function refreshLockBtn() {
  $('#lockBtn').hidden = !localStorage.getItem('p7.token');
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function formatMs(ms) {
  if (!Number.isFinite(ms)) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function showError(message) {
  const box = $('#error');
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  $('#error').hidden = true;
}

/* ---------- 启动 ---------- */

async function boot() {
  bindEvents();
  refreshLockBtn();
  applyMode(state.mode);
  if (offlineData) { bootOffline(); return; }
  if (IS_FILE) { bootFileFallback(); return; }
  if (!(await ensureAuth())) {
    const service = $('#service');
    service.classList.add('is-dead');
    $('#serviceText').textContent = '需要口令';
    renderExamples(FALLBACK_EXAMPLES);
    return;
  }
  try {
    const [health, scenes, examples] = await Promise.all([
      fetch('/api/health', { headers: authHeaders() }).then((r) => r.json()),
      fetch('/api/scenes', { headers: authHeaders() }).then((r) => r.json()),
      fetch('/api/examples', { headers: authHeaders() }).then((r) => r.json()),
    ]);
    state.llmReady = Boolean(health.llm_configured);
    renderStats(health.counts || {});
    renderExamples((examples.examples && examples.examples.length) ? examples.examples : FALLBACK_EXAMPLES);
    const total = (scenes.areas || []).reduce((sum, a) => sum + a.routes, 0);
    const service = $('#service');
    service.classList.add('is-live');
    $('#serviceText').textContent = state.llmReady ? '在线 · 双模式' : '在线 · 规则模式';
    if (total) $('#statRoutes').textContent = String(total);
    $("#llmMode").disabled = !state.llmReady;
    $("#llmMode").title = state.llmReady ? '' : '服务端未配置模型密钥';
    if (!state.llmReady && state.mode === 'llm') applyMode('rule');
  } catch (err) {
    // 接口 500 属于服务异常，不能悄悄降级成回放；只有后端确实不在才读离线数据
    if (await serviceAlive()) {
      const service = $('#service');
      service.classList.add('is-dead');
      $('#serviceText').textContent = '服务异常';
      $("#llmMode").disabled = true;
      showError('后端在运行，但接口返回异常。看服务端日志（journalctl -u p7 -n 50），或稍后刷新。');
      return;
    }
    // 没有后端但同目录有 offline.json（纯静态托管）时，直接降级到回放
    const fallback = await loadOfflineData();
    if (fallback) { bootOffline(fallback); return; }
    const service = $('#service');
    service.classList.add('is-dead');
    $('#serviceText').textContent = '服务未连接';
    renderExamples(FALLBACK_EXAMPLES);
    $("#llmMode").disabled = true;
    showError('连不上后端。运行 python src/P7旅游规划Agent/serve.py 启动本地服务，'
      + '或打开 outputs/P7/行迹-离线预览.html 看七条现成结果。');
  }
}

/* ---------- 离线预览 ---------- */

async function serviceAlive() {
  try {
    const response = await fetch('/api/health', { headers: authHeaders() });
    return response.ok || response.status === 401;   // 401 说明服务在，只是要口令
  } catch {
    return false;
  }
}

async function loadOfflineData() {
  if (offlineData) return offlineData;
  try {
    const response = await fetch('./offline.json', { cache: 'no-store' });
    if (!response.ok) return null;
    const data = await response.json();
    return (data && Array.isArray(data.scenes) && data.scenes.length) ? data : null;
  } catch {
    return null;
  }
}

function bootOffline(data) {
  if (data) offlineData = data;
  state.offline = true;
  state.llmReady = false;
  const meta = offlineData.meta || {};
  const scenes = offlineData.scenes || [];
  renderStats(meta.counts || {});
  renderExamples(scenes.map((item) => item.input), scenes.length);

  const service = $('#service');
  service.classList.remove('is-dead', 'is-live');
  service.classList.add('is-offline');
  $('#serviceText').textContent = '离线预览';
  $('#lockBtn').hidden = true;
  $('#llmMode').disabled = true;
  $('#llmMode').title = '离线预览不调用模型';
  $('#hint').textContent = '离线预览：点示例，或输入需求匹配已有结果';
  $('#query').placeholder = '离线预览：输入需求，匹配七条已有结果';

  const note = $('#offlineNote');
  note.innerHTML = `<strong>离线预览</strong>：这 ${scenes.length} 条结果是规则策略的真实运行记录，`
    + '内联在这一个文件里，不连后端、不调模型。要自由提问，运行 '
    + '<code>python src/P7旅游规划Agent/serve.py</code> 启动本地服务。';
  note.hidden = false;
  if (state.mode !== 'rule') applyMode('rule');

  const empty = $('#empty').querySelector('p');
  if (empty) empty.textContent = '点上面的示例，会展开完整的行程卡与决策过程。';
}

function bootFileFallback() {
  state.offline = true;
  const service = $('#service');
  service.classList.add('is-dead');
  $('#serviceText').textContent = '未连接';
  $('#llmMode').disabled = true;
  renderExamples(FALLBACK_EXAMPLES);
  showError('这是前端源文件，双击打开没有数据。请打开 outputs/P7/行迹-离线预览.html，'
    + '或运行 python src/P7旅游规划Agent/serve.py 启动本地服务。');
}

const DAY_PATTERNS = [[/(两|二|2)\s*天/, 2], [/(三|3)\s*天/, 3], [/(四|4)\s*天/, 4], [/(一|1)\s*天/, 1]];
const PEOPLE_PATTERNS = [
  { word: '老人', keys: ['老人', '爸妈', '父母', '长辈', '爷爷', '奶奶', '外公', '外婆'] },
  { word: '孩子', keys: ['孩子', '小孩', '儿童', '宝宝', '亲子', '儿子', '女儿'] },
  { word: '情侣', keys: ['女朋友', '男朋友', '情侣', '对象', '老婆', '老公'] },
];
/* 地名是判断"问的是不是同一件事"最硬的信号：只换了地名的两句，
   字面仍然很像（"去三亚玩三天"对"去西藏玩三天"），必须靠地名分开。 */
const PLACES = [
  { key: '九寨', words: ['九寨沟', '九寨'] },
  { key: '广州', words: ['广州', '香江'] },
  { key: '珠海', words: ['珠海', '海洋王国', '横琴'] },
  { key: '长隆', words: ['长隆'] },
  { key: '三亚', words: ['三亚'] },
];

function detectPlace(text) {
  const value = String(text || '');
  for (const place of PLACES) {
    if (place.words.some((word) => value.includes(word))) return place.key;
  }
  return '';
}


function wantDays(text) {
  for (const [pattern, days] of DAY_PATTERNS) {
    if (pattern.test(text)) return days;
  }
  return null;
}

function wantPeople(text) {
  for (const item of PEOPLE_PATTERNS) {
    if (item.keys.some((key) => text.includes(key))) return item.word;
  }
  return null;
}

function bigrams(text) {
  const clean = String(text || '').replace(/[\s，。、,.!?！？:：;；"'“”‘’()（）\-—]/g, '');
  const out = new Set();
  for (let i = 0; i < clean.length - 1; i += 1) out.add(clean.slice(i, i + 2));
  return out;
}

function scoreScene(query, scene) {
  const want = bigrams(query);
  const have = bigrams(scene.input);
  if (!want.size || !have.size) return 0;
  let hit = 0;
  want.forEach((gram) => { if (have.has(gram)) hit += 1; });
  let score = hit / want.size;             // 以查询覆盖率为准，短查询不会被长句带偏
  const queryDays = wantDays(query);
  if (queryDays && scene.days && queryDays !== scene.days) score -= 0.22;
  const queryPeople = wantPeople(query);
  if (queryPeople && scene.visitor && queryPeople !== scene.visitor) score -= 0.12;

  const queryPlace = detectPlace(query);
  const scenePlace = detectPlace(`${scene.area || ''} ${scene.input || ''}`);
  if (queryPlace) {
    if (queryPlace === scenePlace) score += 0.25;
    else if (queryPlace === '长隆' && (scenePlace === '广州' || scenePlace === '珠海')) score += 0.15;
    else score -= 0.45;
  }
  return score;
}

function matchScene(query, scenes) {
  const text = query.trim();
  let best = null;
  let bestScore = 0;
  scenes.forEach((scene) => {
    const score = text === scene.input ? 1.5 : scoreScene(text, scene);
    if (score > bestScore) { bestScore = score; best = scene; }
  });
  // 没有地名线索时只靠字面，阈值抬高，避免"去西藏"配上"去三亚"
  const floor = detectPlace(text) ? 0.34 : 0.6;
  if (!best || bestScore < floor) return null;
  return { scene: best, score: bestScore, exact: text === best.input };
}

function offlineRun(query) {
  const scenes = offlineData.scenes || [];
  const match = matchScene(query, scenes);
  clearError();
  if (!match) {
    failRun(`离线预览只含 ${scenes.length} 条预置结果，没有与「${query}」接近的一条。`);
    $('#resultMeta').textContent = '点上面的示例看完整效果，自由提问请启动本地服务。';
    return;
  }
  const result = JSON.parse(JSON.stringify(match.scene.result));
  startRun(query);
  finishRun(result);
  const summary = result.plan
    ? `${result.plan.steps.length} 步`
    : (STATUS_TEXT[result.status] || result.status || '无行程');
  $('#resultMeta').textContent = match.exact
    ? `离线回放 · ${summary}`
    : `离线回放 · 最接近「${match.scene.input}」`;
  if (!match.exact) {
    const card = document.createElement('div');
    card.className = 'card notice';
    card.innerHTML = `<p>离线预览没有与你的输入完全一致的结果，下面这条最接近，仅用于查看界面。</p>`;
    $('#planCol').prepend(card);
  }
}


function renderStats(counts) {
  const map = { statRoutes: counts.routes, statDocs: counts.documents, statRules: counts.rules };
  Object.entries(map).forEach(([id, value]) => {
    if (Number.isFinite(value)) $(`#${id}`).textContent = value.toLocaleString('zh-CN');
  });
  $('#stats').hidden = false;
}

function renderExamples(list, limit = 4) {
  const box = $('#examples');
  box.innerHTML = '';
  list.slice(0, limit).forEach((text) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chip';
    button.textContent = text;
    button.addEventListener('click', () => {
      const input = $('#query');
      input.value = text;
      input.dispatchEvent(new Event('input'));
      input.focus();
    });
    box.appendChild(button);
  });
}

/* ---------- 输入区 ---------- */

function bindEvents() {
  const input = $('#query');
  input.addEventListener('input', () => {
    $('#counter').textContent = `${input.value.length}/200`;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });

  document.querySelectorAll('.mode').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.disabled) return;
      applyMode(button.dataset.mode);
    });
  });

  $('#lockBtn').addEventListener('click', async () => {
    localStorage.removeItem('p7.token');
    await ensureAuth();
  });
  $('#submit').addEventListener('click', submit);
  $('#cancel').addEventListener('click', cancel);
  $('#toggleTrace').addEventListener('click', toggleTrace);
  $('#copyAnswer').addEventListener('click', copyAnswer);
  $('#downloadAnswer').addEventListener('click', downloadAnswer);

  input.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      submit();
    }
    if (event.key === 'Escape' && state.running) cancel();
  });

  window.addEventListener('beforeunload', () => {
    if (state.source) state.source.close();
  });
}

function applyMode(mode) {
  state.mode = mode;
  localStorage.setItem('p7.mode', mode);
  document.querySelectorAll('.mode').forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-checked', String(active));
  });
}

/* ---------- 提交 ---------- */

async function submit() {
  if (state.running) return;
  const query = $('#query').value.trim();
  if (query.length < 2) {
    showError('先写一句需求，比如“带爸妈去九寨沟玩一天”。');
    $('#query').focus();
    return;
  }
  if (state.mode === 'llm' && !state.llmReady) {
    showError('服务端没有配置模型密钥，先用规则模式。');
    return;
  }
  if (state.offline) { offlineRun(query); return; }
  if (!(await ensureAuth())) return;
  clearError();
  startRun(query);
  if (window.EventSource) streamRun(query); else fetchRun(query);
}

function startRun(query) {
  state.running = true;
  state.result = null;
  state.startedAt = performance.now();
  $('#submit').classList.add('is-busy');
  $('#submit').disabled = true;
  $('#cancel').hidden = false;
  $('#empty').hidden = true;
  $('#result').hidden = false;
  $('#statusPill').dataset.status = 'running';
  $('#statusPill').textContent = '规划中';
  $('#resultTitle').textContent = '正在读图谱…';
  $('#resultMeta').textContent = `${state.mode === 'llm' ? '大模型决策' : '规则策略'} · ${query}`;
  $('#planCol').innerHTML = `
    <div class="card loading" aria-hidden="true">
      <span class="sk sk-title"></span><span class="sk"></span><span class="sk"></span><span class="sk short"></span>
    </div>`;
  $('#sideCol').innerHTML = '';
  $('#timeline').innerHTML = '';
  $('#timeline').hidden = false;
  $('#turnCount').textContent = '';
  $('#traceCard').hidden = false;
  $('#answerText').textContent = '';
  $('.textcard').hidden = true;
  $('#toggleTrace').textContent = '收起';
  $('#toggleTrace').setAttribute('aria-expanded', 'true');
  // 手机上结果区在输入框下面，提交后把视线带过去
  if (window.innerWidth < 900) {
    $('#result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function endRun() {
  state.running = false;
  $('#submit').classList.remove('is-busy');
  $('#submit').disabled = false;
  $('#cancel').hidden = true;
}

function cancel() {
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  endRun();
  $('#statusPill').dataset.status = 'no_data';
  $('#statusPill').textContent = '已停止';
  $('#resultTitle').textContent = '已停止接收';
  $('#resultMeta').textContent = '服务端可能仍在跑完当前这一轮，结果不会写回页面。';
}

/* ---------- 两种传输 ---------- */

function streamRun(query) {
  const url = withToken(`/api/plan/stream?query=${encodeURIComponent(query)}&mode=${state.mode}`);
  const source = new EventSource(url);
  state.source = source;

  source.addEventListener('meta', (event) => {
    const data = JSON.parse(event.data);
    if (data.mode) applyMode(data.mode);
  });

  source.addEventListener('turn', (event) => {
    appendTurn(JSON.parse(event.data), false);
  });

  source.addEventListener('result', (event) => {
    source.close();
    state.source = null;
    finishRun(JSON.parse(event.data));
  });

  source.addEventListener('failure', (event) => {
    source.close();
    state.source = null;
    const data = JSON.parse(event.data);
    failRun(data.message || '规划过程出错');
  });

  source.onerror = () => {
    if (!state.running) return;
    source.close();
    state.source = null;
    fetchRun(query);
  };
}

async function fetchRun(query) {
  try {
    const response = await fetch('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ query, mode: state.mode, max_turns: 6 }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      if (response.status === 401) {
        localStorage.removeItem('p7.token');
        if (await ensureAuth()) { fetchRun(query); return; }
      }
      throw new Error(detail.detail || `请求失败（${response.status}）`);
    }
    finishRun(await response.json());
  } catch (err) {
    failRun(err.message || '网络异常');
  }
}

function failRun(message) {
  endRun();
  showError(message);
  state.result = null;
  $('#statusPill').dataset.status = 'no_data';
  $('#statusPill').textContent = '未完成';
  $('#resultTitle').textContent = '这次没跑出结果';
  $('#resultMeta').textContent = '换个问法，或启动本地服务再试一次。';
  $('#planCol').innerHTML = '';
  $('#sideCol').innerHTML = '';
  $('#timeline').innerHTML = '';
  $('#traceCard').hidden = true;
  $('#answerText').textContent = '';
  $('.textcard').hidden = true;
}

/* ---------- 渲染结果 ---------- */

function finishRun(result) {
  endRun();
  state.result = result;
  const plan = result.plan;
  const status = result.status || 'no_data';

  $('#statusPill').dataset.status = status;
  $('#statusPill').textContent = STATUS_TEXT[status] || status;
  $('#resultTitle').textContent = plan
    ? `${plan.area_short} · ${dayWord(plan.days_actual)} ${plan.steps.length} 步`
    : (STATUS_TITLE[status] || '这次没排出方案');

  const meta = [];
  meta.push(result.mode === 'llm' ? '大模型决策' : '规则策略');
  meta.push(`用时 ${formatMs(result.elapsed_ms)}`);
  if (result.route_id) meta.push(`路线 ${result.route_id}`);
  $('#resultMeta').textContent = meta.join(' · ');

  renderPlan(plan);
  renderSide(plan, result.slots || {});
  renderTimeline(result.turns || []);
  $('#answerText').textContent = result.answer || '';
  $('.textcard').hidden = !result.answer;

  if (status !== 'ok' && status !== 'partial') {
    const card = document.createElement('div');
    card.className = 'card notice';
    card.innerHTML = `<p>${escapeHtml(result.answer || '')}</p>`;
    $('#planCol').prepend(card);
  }
}

function dayWord(n) {
  return { 1: '一日', 2: '两日', 3: '三日' }[n] || `${n} 天`;
}

function renderPlan(plan) {
  const box = $('#planCol');
  box.innerHTML = '';
  if (!plan || !plan.day_groups || !plan.day_groups.length) return;

  plan.day_groups.forEach((steps, index) => {
    const section = document.createElement('section');
    section.className = 'day';
    section.innerHTML = `
      <div class="day-head">
        <span class="day-no">第 ${index + 1} 天</span>
        <span class="day-title">${steps.length} 步${daySpan(steps)}</span>
      </div>
      <ol class="steps"></ol>`;
    const list = section.querySelector('.steps');
    steps.forEach((step) => list.appendChild(renderStep(step)));
    box.appendChild(section);
  });
}

function daySpan(steps) {
  const times = steps.map((s) => s.time).filter(Boolean).sort();
  if (!times.length) return ' · 原文未标时刻';
  return ` · ${times[0]}—${times[times.length - 1]}`;
}

function renderStep(step) {
  const li = document.createElement('li');
  li.className = 'step';
  const meta = [];
  if (step.time) meta.push(`<span><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor"/><path d="M8 4.5V8l2.4 1.6" fill="none" stroke="currentColor" stroke-linecap="round"/></svg>${escapeHtml(step.time)}</span>`);
  if (step.transport) meta.push(`<span>${escapeHtml(step.transport)}</span>`);
  li.innerHTML = `
    <span class="step-idx">${escapeHtml(step.step)}</span>
    <div class="step-body">
      <div class="step-spot">${escapeHtml(step.spot)}</div>
      ${meta.length ? `<div class="step-meta">${meta.join('')}</div>` : ''}
      ${step.tag ? `<span class="tag">${escapeHtml(step.tag)}</span>` : ''}
    </div>`;
  return li;
}

function renderSide(plan, slots) {
  const box = $('#sideCol');
  box.innerHTML = '';
  if (!plan) return;

  const params = document.createElement('section');
  params.className = 'side-card card';
  const rows = [
    ['景区', plan.area_short || plan.area],
    ['人群', plan.visitor || '未指定'],
    ['天数', plan.days === plan.days_actual
      ? `${plan.days_actual} 天`
      : `你说 ${plan.days} 天，实际需 ${plan.days_actual} 天`],
    ['步数', `${plan.steps.length} 步`],
  ];
  params.innerHTML = `<h3>行程参数</h3><dl class="kv">${rows.map(
    ([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('')}</dl>`;
  box.appendChild(params);

  if (plan.tips && plan.tips.length) {
    const tips = document.createElement('section');
    tips.className = 'side-card card';
    tips.innerHTML = `<h3>注意事项</h3><ul class="tips">${plan.tips.map(
      (t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>`;
    box.appendChild(tips);
  }

  if (plan.notes && plan.notes.length) {
    const notes = document.createElement('section');
    notes.className = 'side-card card';
    notes.innerHTML = `<h3>提醒</h3><ul class="tips notes">${plan.notes.map(
      (t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>`;
    box.appendChild(notes);
  }
}

function renderTimeline(turns) {
  const list = $('#timeline');
  list.innerHTML = '';
  turns.forEach((turn) => appendTurn(turn, true));
  $('#turnCount').textContent = `${Math.max(0, turns.length - 1)} 轮工具调用`;
}

function appendTurn(turn, silent) {
  const list = $('#timeline');
  if (!list) return;
  const li = document.createElement('li');
  const isFinal = turn.action === 'final' || turn.action === 'ask';
  li.className = `turn${isFinal ? ' is-final' : ''}`;
  li.innerHTML = `
    <span class="turn-dot" aria-hidden="true"></span>
    <div class="turn-top">
      <span class="turn-no">${String(turn.turn).padStart(2, '0')}</span>
      <span class="action">${escapeHtml(turn.action || '')}</span>
    </div>
    <p class="turn-thought">${escapeHtml(turn.thought || '')}</p>
    ${turn.args ? `<p class="turn-args">${escapeHtml(turn.args)}</p>` : ''}
    <p class="turn-obs">${escapeHtml(turn.observation || '')}</p>`;
  list.appendChild(li);
  if (!silent) {
    li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    $('#turnCount').textContent = `${Math.max(0, list.children.length - 1)} 轮工具调用`;
  }
}

function toggleTrace() {
  const list = $('#timeline');
  const button = $('#toggleTrace');
  const open = list.hidden;
  list.hidden = !open;
  button.textContent = open ? '收起' : '展开';
  button.setAttribute('aria-expanded', String(open));
}

/* ---------- 复制与下载 ---------- */

async function copyAnswer() {
  if (!state.result) return;
  const text = state.result.answer || '';
  try {
    await navigator.clipboard.writeText(text);
    flash('#copyAnswer', '已复制');
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    flash('#copyAnswer', '已复制');
  }
}

function downloadAnswer() {
  if (!state.result) return;
  const result = state.result;
  const plan = result.plan;
  const lines = [];
  lines.push(`# ${plan ? `${plan.area_short} · ${dayWord(plan.days_actual)}` : '行程'}`);
  lines.push('');
  lines.push(result.answer || '');
  if (plan && plan.steps.length) {
    lines.push('');
    lines.push('| 天 | 序 | 景点 | 时间 | 交通 | 标注 |');
    lines.push('| --- | --- | --- | --- | --- | --- |');
    plan.day_groups.forEach((steps, index) => {
      steps.forEach((s) => lines.push(
        `| ${index + 1} | ${s.step} | ${s.spot} | ${s.time || '—'} | ${s.transport || '—'} | ${s.tag || '—'} |`));
    });
  }
  lines.push('');
  lines.push('> 数据来自历史游记与官网资料，时刻与开放状态请以景区当日公告为准。');
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `行程-${plan ? plan.area_short : 'plan'}-${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

function flash(selector, text) {
  const node = $(selector);
  const original = node.textContent;
  node.textContent = text;
  setTimeout(() => { node.textContent = original; }, 1400);
}

boot();
