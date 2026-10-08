<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { activeRun, type AgentEvent, type AgentRun } from "../../agent";
import { dateLabel } from "../../api";
const props = defineProps<{ run: AgentRun; events: AgentEvent[] }>();
const now = ref(Date.now());
const timer = setInterval(() => {
  if (activeRun(props.run.status)) now.value = Date.now();
}, 1000);
onUnmounted(() => clearInterval(timer));
const duration = computed(() => {
  const start = props.run.started_at ?? props.run.created_at;
  const end = props.run.finished_at
    ? Date.parse(props.run.finished_at)
    : now.value;
  const seconds = Math.max(0, Math.round((end - Date.parse(start)) / 1000));
  return Number.isFinite(seconds)
    ? `${Math.floor(seconds / 60)}分${seconds % 60}秒`
    : "未提供";
});
const labels: Record<string, string> = {
  queued: "排队",
  running: "开始执行",
  check: "检查数据范围",
  plan: "生成计划",
  model_start: "请求模型",
  model_end: "模型响应",
  model: "请求模型",
  usage: "模型响应",
  validate: "报告校验",
  tool_start: "执行工具",
  tool_end: "工具完成",
  report: "生成报告",
  validated: "报告校验",
  repair: "受限修复",
  budget: "预算边界",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  cancel_requested: "请求取消",
};
function description(value: string) {
  const tools: Record<string, string> = {
    get_schema: "查看数据结构",
    filter_data: "筛选记录",
    group_by: "分组统计",
    calculate_metric: "计算指标",
    search_evidence: "检索证据",
  };
  return value.replace(
    /get_schema|filter_data|group_by|calculate_metric|search_evidence/g,
    (name) => tools[name]!,
  );
}
</script>
<template>
  <section class="agent-progress" aria-label="执行时间线">
    <dl class="run-statistics">
      <div>
        <dt>{{ run.started_at ? "执行耗时" : "排队与等待耗时" }}</dt>
        <dd>{{ duration }}</dd>
      </div>
      <div>
        <dt>完成工具调用</dt>
        <dd>{{ run.tool_count }} 次</dd>
      </div>
      <div>
        <dt>模型调用</dt>
        <dd>
          {{
            run.usage.model_calls === undefined
              ? "未提供"
              : `${run.usage.model_calls} 次`
          }}
        </dd>
      </div>
      <div>
        <dt>服务商 Token 统计</dt>
        <dd>
          {{ run.usage.total_tokens ?? "未提供"
          }}<small
            v-if="run.usage.total_tokens !== undefined && !run.usage.complete"
            >（部分请求数据）</small
          >
        </dd>
      </div>
    </dl>
    <h2>分析计划</h2>
    <ol v-if="run.plan.length">
      <li v-for="(step, i) in run.plan" :key="i">{{ step }}</li>
    </ol>
    <p v-else class="muted">尚未生成计划。</p>
    <h2>执行记录</h2>
    <p v-if="!events.length" class="muted">尚无执行记录。</p>
    <ol class="run-timeline">
      <li v-for="event in events" :key="event.sequence">
        <span class="timeline-stage">{{
          labels[event.kind] ?? "状态更新"
        }}</span>
        <p>{{ description(event.message) }}</p>
        <time :datetime="event.created_at">{{
          dateLabel(event.created_at)
        }}</time>
      </li>
    </ol>
  </section>
</template>
