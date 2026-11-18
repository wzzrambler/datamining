<script setup>
import { computed, ref, watch } from 'vue';
import { json, params, requestId } from './api';
import { vSlidingDrawer } from './slidingDrawer';

const props=defineProps({plan:{type:Object,required:true},drawerOpen:Boolean});
const emit=defineEmits(['show-entity','chat-edit','updated','load-current','close-drawer']);
const error=ref('');
const busy=ref(false);
const insert=ref(null);
const q=ref('');
const results=ref([]);
const copied=ref(false);
const current=computed(() => props.plan.version===props.plan.current_version);

watch(() => [props.plan.itinerary_id,props.plan.version], () => { insert.value=null; error.value=''; });
async function apply(operations) {
  if (busy.value || !current.value) return;
  busy.value=true;
  error.value='';
  try {
    const response=await json(`/itineraries/${props.plan.itinerary_id}`, { method:'PATCH',
      body:{client_edit_id:requestId(),base_version:props.plan.version,operations} });
    emit('updated',response.itinerary);
    insert.value=null;
  } catch (e) {
    error.value=e.message;
  } finally {
    busy.value=false;
  }
}
function begin(day,item) {
  q.value='';
  results.value=[];
  insert.value={day,item,position:'after'};
}
async function lookup() {
  if (!q.value.trim()) return;
  try {
    const data=await json('/knowledge/pois'+params({data_version:props.plan.data_version,q:q.value.trim()}));
    results.value=data.items;
    if (!results.value.length) error.value='没有找到匹配景点';
    else error.value='';
  } catch (e) { error.value=e.message; }
}
function add(poi) {
  const {day,item,position}=insert.value;
  return apply([{op:'insert',day_id:day.day_id,
    anchor:item ? {[`${position}_item_id`]:item.item_id} : {day_start:true},
    item:{type:'poi_visit',poi_id:poi.poi_id,recommendation_reason:'按用户选择加入行程',
      evidence_refs:[{kind:'node',id:poi.poi_id}]}}]);
}
async function copy() {
  const text = [props.plan.title, ...props.plan.days.flatMap(day => [
    `第 ${day.day_no} 天`,
    ...day.items.map(item => `${item.order}. ${item.name || item.type}${item.recommendation_reason ? ' · ' + item.recommendation_reason : ''}`)
  ])].join('\n');
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
    } else {
      const previous = document.activeElement;
      const field = document.createElement('textarea');
      field.value = text;
      field.readOnly = true;
      field.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(field);
      try {
        field.select();
        if (!document.execCommand('copy')) throw new Error('当前浏览器无法复制行程');
      } finally {
        field.remove();
        previous?.focus();
      }
    }
    copied.value = true;
  } catch (e) {
    error.value = e.message;
  }
}
</script>

<template>
  <aside v-sliding-drawer="{ open: drawerOpen, side: 1, mobileOnly: true, close: () => emit('close-drawer') }"
         class="itinerary" aria-label="行程">
    <div class="card plan-head">
      <div class="actions"><span class="pill" :class="{warning: plan.status==='partial'}">{{ plan.status==='partial'?'有待确认':'已保存' }}</span><span class="muted">第 {{ plan.version }} 版</span></div>
      <h2>{{ plan.title }}</h2>
      <div class="actions"><button class="quiet" @click="copy">{{ copied?'已复制':'复制行程' }}</button><button v-if="!current || error" class="quiet" @click="emit('load-current')">读取最新版本</button></div>
      <ul v-if="plan.warnings.length" class="warnings"><li v-for="w in plan.warnings" :key="w">{{ w }}</li></ul>
      <ul v-if="plan.unresolved_items.length" class="warnings"><li v-for="(w,i) in plan.unresolved_items" :key="i">{{ w.reason || w.description || w.step_id || '该步骤的景点关联待确认' }}</li></ul>
    </div>
    <p v-if="error" role="alert" class="banner">{{ error }}</p>
    <section v-for="day in plan.days" :key="day.day_id" class="card day">
      <header><h3>第 {{ day.day_no }} 天</h3><span class="muted">{{ day.date || (day.schedule_basis === 'source' ? '来源日次' : '建议安排') }}</span></header>
      <ol>
        <li v-for="item in day.items" :key="item.item_id" class="visit">
          <span class="number">{{ item.order }}</span>
          <div>
            <button v-if="item.poi_id || item.leg_id" class="name-link" @click="emit('show-entity',item)">{{ item.name || item.poi_id || item.leg_id }}</button>
            <strong v-else>{{ item.name || '备注' }}</strong>
            <span v-if="item.fields?.planned_time" class="muted"> · {{ item.fields.planned_time }}</span>
            <span v-if="item.fields?.optional" class="muted"> · 可选</span>
            <p v-if="item.recommendation_reason">{{ item.recommendation_reason }}</p>
            <details v-if="current" class="item-edit">
              <summary>编辑此项</summary>
              <div class="actions">
                <button class="quiet" @click="emit('chat-edit', item, day)">在对话中修改</button>
                <button v-if="item.type==='poi_visit'" class="quiet" :disabled="busy" @click="apply([{op:'update_fields',target:{item_id:item.item_id},fields:{optional:!item.fields.optional}}])">{{ item.fields.optional?'设为必排':'设为可选' }}</button>
                <button class="quiet" :disabled="busy" @click="begin(day,item)">插入</button>
                <button class="quiet danger" :disabled="busy" @click="apply([{op:'delete',target:{item_id:item.item_id}}])">删除</button>
              </div>
            </details>
          </div>
        </li>
      </ol>
      <button v-if="current && !day.items.length" class="quiet" @click="begin(day,null)">添加景点</button>
      <form v-if="insert?.day.day_id===day.day_id" class="insert-form" @submit.prevent="lookup">
        <label>查找要插入的景点<input v-model="q" placeholder="输入景点名称"></label>
        <label v-if="insert.item">位置<select v-model="insert.position"><option value="before">在此项之前</option><option value="after">在此项之后</option></select></label>
        <div class="actions"><button class="primary" type="submit">查找</button><button class="quiet" type="button" @click="insert=null">关闭</button></div>
        <ul><li v-for="poi in results" :key="poi.poi_id"><button class="name-link" type="button" :disabled="busy" @click="add(poi)">{{ poi.name }} · {{ poi.scenic_code }}</button></li></ul>
      </form>
    </section>
  </aside>
</template>
