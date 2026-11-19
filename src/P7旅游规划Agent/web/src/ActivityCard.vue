<script setup>
import { computed } from 'vue';
const props = defineProps({ activity: { type: Object, required: true } });
const status = computed(() => ({
  running: '查询中', ok: '已完成', no_data: '暂无记录', error: '调用失败',
})[props.activity.stage] || '已返回');
</script>

<template>
  <details class="activity-card">
    <summary>
      <strong>{{ activity.title || activity.label || '工具调用' }}</strong>
      <span v-if="activity.scope" class="activity-scope">{{ activity.scope }}</span>
      <span class="activity-status" :class="activity.stage">{{ status }}<template v-if="activity.result_count != null"> · {{ activity.result_count }} 条</template></span>
      <span class="activity-caret" aria-hidden="true"></span>
    </summary>
    <div class="activity-details">
      <p v-if="activity.intent"><strong>决策摘要</strong> {{ activity.intent }}</p>
      <p v-if="activity.label && (activity.stage === 'error' || activity.stage === 'no_data' || activity.label !== activity.title)">
        <strong>结果</strong> {{ activity.label }}
      </p>
    </div>
  </details>
</template>
