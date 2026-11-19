<script setup>
import { ref, nextTick } from 'vue';
import { json, params } from './api';

const modal=ref();
const current=ref(null);
const loading=ref(false);
const error=ref('');
const evidence=ref(null);
let key=0;
const title=ref('');
async function open(type,id,scope={}) {
  const revision=++key;
  current.value={id,type,scope};
  evidence.value=null;
  error.value='';
  loading.value=true;
  title.value=type==='route'?'路线详情':'景点详情';
  await nextTick();
  if (!modal.value.open) modal.value.showModal();
  try {
    const kind=type==='route'?'routes':'entities';
    const item=await json(`/knowledge/${kind}/${encodeURIComponent(id)}`+params(scope));
    if (revision!==key) return;
    current.value.data=item;
    if (item.name) title.value=item.name;
  } catch (e) {
    if (revision===key) error.value=e.message;
  } finally {
    if (revision===key) loading.value=false;
  }
}

async function sources(refId,append=false) {
  const active=current.value;
  if (!active) return;
  try {
    const data=await json('/knowledge/sources'+params({
      ...active.scope, ref_id:refId, entity_id:active.type==='entity'?active.id:null,
      route_id:active.type==='route'?active.id:active.scope.route_id,
      cursor:append?evidence.value?.next_cursor:null,
    }));
    if (active!==current.value) return;
    evidence.value={...data,refId,sources:append?[...evidence.value.sources,...data.sources]:data.sources};
  } catch (e) { error.value=e.message; }
}
async function moreRoute() {
  const active=current.value;
  try {
    const extra=await json(`/knowledge/routes/${active.id}`+params({...active.scope,cursor:active.data.next_cursor}));
    if (current.value===active) {
      active.data.steps.push(...extra.steps);
      active.data.next_cursor=extra.next_cursor;
    }
  } catch (e) { error.value=e.message; }
}
function sourceHref(url) { return /^https?:\/\//i.test(url || '') ? url:null; }
defineExpose({open});
</script>

<template>
  <dialog ref="modal" class="detail card" aria-labelledby="detail-title" @close="key++">
    <header><h2 id="detail-title">{{ title }}</h2><form method="dialog"><button class="quiet" aria-label="关闭详情">关闭</button></form></header>
    <p v-if="loading" class="muted">正在读取 P6 资料…</p>
    <p v-if="error" class="banner" role="alert">{{ error }}</p>
    <div v-if="current?.data">
      <p class="source-caption">{{ current.data.historical_notice }}</p>
      <template v-if="current.type==='route'">
        <p v-for="text in current.data.summaries" :key="text">{{ text }}</p>
        <p v-if="!current.data.summaries.length" class="muted">该路线的 P6 简介缺失，可查看步骤。</p>
        <ol class="route-steps">
          <li v-for="s in current.data.steps" :key="s.step_id">
            <span class="muted">{{ s.period || '' }} {{ s.historical_time.join('、') }}</span>
            <div v-if="s.pois.length" class="actions"><button v-for="p in s.pois" :key="p.poi_id" class="name-link" @click="open('entity',p.poi_id,{data_version:current.data.data_version,route_id:current.id,step_id:s.step_id})">{{ p.name }}</button></div>
            <span v-else>尚未匹配景点</span>
            <button class="quiet" @click="sources(s.step_id)">本步骤依据</button>
          </li>
        </ol>
        <button v-if="current.data.next_cursor" class="quiet" @click="moreRoute">更多步骤</button>
      </template>
      <template v-else>
        <h3>时间汇总</h3>
        <ul v-if="current.data.time_summaries.length"><li v-for="(t,i) in current.data.time_summaries" :key="i">{{ t.values.map(x=>x.time_text).join('、') }} <button class="quiet" @click="sources(t.evidence_refs[0].id)">依据</button></li></ul>
        <p v-else class="muted">暂无适用于当前范围的时间汇总。</p>
        <h3>建议与限制</h3>
        <div v-for="(t,i) in current.data.advice_themes" :key="'t'+i" class="detail-entry"><strong>{{ t.title }}</strong><p>{{ t.summary }}</p><button class="quiet" @click="sources(t.evidence_refs[0].id)">查看依据</button></div>
        <div v-for="(t,i) in current.data.advice_summaries" :key="'a'+i" class="detail-entry"><p>{{ t.summary }}</p><button class="quiet" @click="sources(t.evidence_refs[0].id)">查看依据</button></div>
        <p v-if="!current.data.advice_themes.length && !current.data.advice_summaries.length" class="muted">暂无适用于当前范围的建议。</p>
      </template>
      <button class="chip" @click="sources(null)">查看来源</button>
    </div>
    <section v-if="evidence" class="evidence" aria-label="P6 原文依据">
      <h3>来源资料</h3>
      <p v-if="!evidence.sources.length" class="muted">暂无可定位的资料。</p>
      <article v-for="(s,i) in evidence.sources" :key="i" class="detail-entry">
        <a v-if="sourceHref(s.source_url)" :href="sourceHref(s.source_url)" rel="noopener noreferrer" target="_blank">{{ s.title }}</a>
        <strong v-else>{{ s.title }}</strong>
        <p v-if="s.excerpt">{{ s.excerpt }}</p>
        <small class="muted">{{ s.doc_id }}<span v-if="s.start!=null"> · {{ s.start }}–{{ s.end }}</span></small>
      </article>
      <button v-if="evidence.next_cursor" class="quiet" @click="sources(evidence.refId,true)">更多来源</button>
    </section>
  </dialog>
</template>
