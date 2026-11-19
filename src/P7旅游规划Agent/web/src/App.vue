<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import MarkdownIt from 'markdown-it';
import { json, params } from './api';
import { useChat } from './useChat';
import Itinerary from './Itinerary.vue';
import Details from './Details.vue';
import ActivityCard from './ActivityCard.vue';
import { vSlidingDrawer } from './slidingDrawer';
import { buildTimeline, mergeActivityEvents } from './timeline';

const { conversations, nextConversation, current, messages, activities, before, plan, drafts, runs, error, status,
  boot, list, choose, refresh, showPlan, send, stop, older, reconnect } = useChat();
const markdown = new MarkdownIt({ html: false, linkify: false });
const examples = ['带爸妈去九寨沟，一天怎么安排？','广州长隆有哪些适合亲子的路线？','帮我排珠海长隆两天的行程'];
const details = ref();
const historyOpen = ref(false);
const itineraryOpen = ref(false);
const field = ref();
const messageList = ref();
const follow = ref(true);
const run = computed(() => runs[current.value?.id] || {});
const timeline = computed(() => buildTimeline(messages.value,
  mergeActivityEvents(activities.value, run.value.activities || [])));
const draft = computed({ get: () => drafts[current.value?.id || 'start'] || '',
  set: text => { drafts[current.value?.id || 'start'] = text; } });

async function safely(fn) {
  try { await fn(); } catch (ex) { error.value = ex.message; }
}
function select(cid) {
  closeDrawers();
  follow.value = true;
  return safely(() => choose(cid));
}
function submit() {
  follow.value = true;
  return send(draft.value);
}
function startExample(text) {
  draft.value = text;
  field.value?.focus();
}
function onKey(e) {
  if (!e.isComposing && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendWithTarget();
  }
}
function capture(e, version) {
  const raw = e.target.closest('a')?.getAttribute('href');
  const entity = raw?.match(/^\/entities\/([a-zA-Z0-9_-]+)$/);
  if (!entity) return;
  e.preventDefault();
  details.value.open('entity', entity[1], { data_version: version || current.value?.data_version });
}
function openRoute(route, version) {
  details.value.open('route', route.route_id, { data_version: version });
}
function toggleHistory() {
  historyOpen.value = !historyOpen.value;
  if (historyOpen.value) itineraryOpen.value = false;
}
function toggleItinerary() {
  itineraryOpen.value = !itineraryOpen.value;
  if (itineraryOpen.value) historyOpen.value = false;
}
function closeDrawers() {
  historyOpen.value = false;
  itineraryOpen.value = false;
}
async function openSavedItinerary(attachment) {
  await showPlan(attachment.itinerary_id, attachment.version);
  itineraryOpen.value = true;
  historyOpen.value = false;
}
function openEntity(item) {
  const id=item.poi_id || item.leg_id;
  if (id) details.value.open('entity', id, { data_version: plan.value?.data_version, route_id:item.route_id, step_id:item.step_id });
}
function editByChat(item, day) {
  itineraryOpen.value=false;
  draft.value=`请修改第 ${day.day_no} 天的${item.name || '这项'}：`;
  editingTarget.value={type:'item',id:item.item_id};
  nextTick(() => field.value?.focus());
}
const editingTarget=ref(null);
watch(() => current.value?.id, () => { editingTarget.value=null; });
function sendWithTarget() {
  if (!editingTarget.value) return submit();
  const target=editingTarget.value;
  editingTarget.value=null;
  follow.value=true;
  return send(draft.value,{target});
}
function scrollToEnd() {
  follow.value=true;
  if (messageList.value) messageList.value.scrollTop=messageList.value.scrollHeight;
}
async function readOlder() {
  const beforeHeight=messageList.value?.scrollHeight || 0;
  await safely(older);
  await nextTick();
  if (messageList.value) messageList.value.scrollTop+=messageList.value.scrollHeight-beforeHeight;
}
watch([() => timeline.value.length, () => run.value.activities?.length, () => run.value.delta], async () => {
  if (follow.value) {
    await nextTick();
    scrollToEnd();
  }
});
onMounted(boot);
</script>

<template>
  <div class="shell">
    <header class="top">
      <button class="brand" @click="select(null)" aria-label="行迹首页">
        <svg width="36" height="36" viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="16.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M26.5 13.5 22 22l-8.5 4.5L18 18z" fill="currentColor"/><circle cx="20" cy="20" r="1.6" fill="var(--panel)"/></svg>
        <span><strong>行迹</strong><small>知识图谱旅游规划</small></span>
      </button>
      <span class="service"><i :class="{live: status?.service_ready}"></i>{{ status?.service_ready ? '在线 · DeepSeek Flash' : status ? '服务未就绪' : '正在连接' }}</span>
    </header>

    <nav class="drawer-controls" aria-label="规划面板">
      <button class="disclosure" type="button" :aria-expanded="historyOpen" aria-controls="chat-history"
              @click="toggleHistory">历史规划 <span class="disclosure-arrow" aria-hidden="true"></span></button>
      <button v-if="plan" class="disclosure itinerary-toggle" type="button" :aria-expanded="itineraryOpen" aria-controls="current-itinerary"
              @click="toggleItinerary">行程规划 <span class="disclosure-arrow" aria-hidden="true"></span></button>
    </nav>

    <div class="layout" :class="{ 'has-plan': plan }">
      <Transition name="drawer-fade">
        <button v-if="historyOpen || (plan && itineraryOpen)" type="button" class="drawer-backdrop" :class="{ 'itinerary-backdrop': !historyOpen }"
                aria-label="关闭侧栏" tabindex="-1" @click="closeDrawers"></button>
      </Transition>
      <aside id="chat-history" v-sliding-drawer="{ open: historyOpen, side: -1, close: closeDrawers }"
             class="rail card" aria-label="会话列表">
        <div class="rail-top">
          <button class="primary" @click="select(null)">＋ 新会话</button>
        </div>
        <div class="conversations">
          <button v-for="c in conversations" :key="c.id" class="conversation" :class="{active: c.id === current?.id}" @click="select(c.id)">{{ c.title }}</button>
          <p v-if="!conversations.length" class="muted">暂无会话</p>
          <button v-if="nextConversation" class="quiet" @click="safely(() => list(true))">更多会话</button>
        </div>
      </aside>

      <main class="workspace">
        <section v-if="!current" class="hero">
          <h1>把散在攻略里的路线，<br>排成适合你的行程。</h1>
          <p>可以先比较路线，再一起排每天去哪里。点开景点，还能查看知识图谱里的建议和来源。</p>
        </section>
        <div v-else ref="messageList" class="messages" role="log" aria-live="polite"
             @scroll="follow = $event.target.scrollHeight - $event.target.clientHeight - $event.target.scrollTop < 100">
          <button v-if="before" class="quiet older" @click="readOlder">加载更早消息</button>
          <template v-for="m in timeline" :key="m.key">
          <ActivityCard v-if="m.kind === 'activity'" :activity="m"/>
          <article v-else :class="['message',m.role]">
            <template v-if="m.role === 'user'">
              <p class="message-author">你</p>
              <p class="user-text">{{ m.markdown }}</p>
            </template>
            <div v-else-if="!m.attachments?.some(a => a.type === 'question' && a.question?.trim() === m.markdown?.trim())"
                 class="markdown" @click="capture($event,m.data_version)" v-html="markdown.render(m.markdown || '')"></div>
            <template v-for="(a,i) in m.attachments" :key="i">
              <div v-if="a.type === 'route_candidates'" class="candidates">
                <section v-for="(r,index) in a.items" :key="r.route_id" class="card candidate">
                  <div class="candidate-label"><span class="number">{{ index + 1 }}</span><span>{{ r.route_class === 'multi_park_overview' ? '跨园概要' : r.route_class === 'timed_thematic_itinerary' ? '专题行程' : '游览序列' }}</span><span class="muted">{{ r.step_count }} 步</span></div>
                  <p>{{ r.summary || '查看路线步骤' }}</p>
                  <small v-if="r.must_visit_missing?.length">该路线未覆盖全部必去点。</small>
                  <div class="actions">
                    <button class="quiet" @click="openRoute(r,m.data_version)">查看路线</button>
                    <button class="quiet" :disabled="run.running || run.disconnected" @click="send('按这条路线帮我规划行程。', { target: { type:'route',id:r.route_id } })">按这条规划</button>
                  </div>
                </section>
              </div>
              <section v-if="a.type === 'question'" class="question-card" aria-label="行迹的提问">
                <header class="question-label">
                  <span>行迹在问</span>
                  <span class="muted">{{ current?.pending_question?.id === m.message_id ? '等待回复' : '历史提问' }}</span>
                </header>
                <p class="question-text">{{ a.question || m.markdown }}</p>
                <div v-if="a.options?.length" class="actions clarifications">
                  <button v-if="current?.pending_question?.id === m.message_id" v-for="option in a.options" :key="option"
                          class="chip" :disabled="run.running || run.disconnected" @click="send(option,{replyTo:m.message_id})">{{ option }}</button>
                  <template v-if="current?.pending_question?.id !== m.message_id">
                    <span v-for="option in a.options" :key="option" class="past-option">{{ option }}</span>
                  </template>
                </div>
              </section>
              <button v-if="a.type === 'itinerary'" class="chip" @click="safely(() => openSavedItinerary(a))">{{ a.title }} · 第 {{ a.version }} 版</button>
            </template>
          </article>
          </template>
          <div v-if="run.delta" class="message assistant preview-text">{{ run.delta }}</div>
        </div>
        <div v-if="!follow && run.running" class="catch-up"><button class="chip" @click="scrollToEnd">查看新消息</button></div>
        <p v-if="error" class="banner" role="alert">{{ error }}</p>
        <p v-if="run.disconnected" class="banner">连接断开 <button class="quiet" @click="reconnect">接续本轮</button></p>

        <form class="composer card" @submit.prevent="sendWithTarget">
          <label class="sr-only" for="question">说说你的旅游需求</label>
          <textarea id="question" ref="field" v-model="draft" rows="2" maxlength="4000" :placeholder="current ? '继续提问或修改行程' : '例如：带爸妈去九寨沟，一天怎么安排？'" @keydown="onKey"></textarea>
          <div v-if="!current" class="actions examples"><button v-for="sample in examples" :key="sample" type="button" class="chip" @click="startExample(sample)">{{ sample }}</button></div>
          <div class="actions compose-footer">
            <span v-if="run.running" class="muted" aria-live="polite">{{ run.stopping ? '正在停止…' : '正在查询…' }}</span>
            <span v-else class="muted" aria-label="快捷键">Ctrl / ⌘ + Enter</span>
            <button v-if="run.running" type="button" class="quiet" :disabled="!run.turn || run.stopping" @click="stop">停止</button>
            <button type="submit" class="primary" :disabled="!draft.trim() || run.running || run.disconnected || !status?.service_ready">发送</button>
          </div>
        </form>
        <p v-if="!current" class="source-caption">资料来自 P6 知识图谱。历史时刻、票价与开放状态请以景区当日公告为准。</p>
      </main>
      <Itinerary v-if="plan" id="current-itinerary" :plan="plan" :drawer-open="itineraryOpen" @close-drawer="closeDrawers" @show-entity="openEntity" @chat-edit="editByChat"
                 @updated="(p) => plan = p" @load-current="safely(() => showPlan(plan.itinerary_id))"/>
    </div>
    <Details ref="details"/>
  </div>
</template>
