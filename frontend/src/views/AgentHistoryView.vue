<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { agentApi, runLabels, type RunSummary } from "../agent";
import { api, message } from "../api";
import SelectField from "../components/SelectField.vue";
import type { Project } from "../types";
const route = useRoute(),
  router = useRouter();
const project = ref<Project | null>(null),
  items = ref<RunSummary[]>([]);
const q = ref(""),
  status = ref(""),
  start = ref(""),
  end = ref("");
const nextCursor = ref<string | null>(null),
  total = ref(0),
  loading = ref(false),
  error = ref("");
const refreshing = ref(0);
const statuses = [
  { value: "", label: "全部状态" },
  ...Object.entries(runLabels).map(([value, label]) => ({ value, label })),
];
let generation = 0;
const value = (key: string) =>
  typeof route.query[key] === "string" ? String(route.query[key]) : "";
watch(
  () => [route.params.id, route.query, refreshing.value],
  async () => {
    const version = ++generation,
      p = String(route.params.id);
    project.value = null;
    items.value = [];
    nextCursor.value = null;
    error.value = "";
    loading.value = true;
    q.value = value("q");
    status.value = value("status");
    start.value = value("start");
    end.value = value("end");
    const params = new URLSearchParams();
    for (const key of ["q", "status", "start", "end", "cursor"])
      if (value(key)) params.set(key, value(key));
    try {
      const [pvalue, result] = await Promise.all([
        api.project(p),
        agentApi.history(p, params),
      ]);
      if (version !== generation) return;
      project.value = pvalue;
      items.value = result.items;
      total.value = result.total;
      nextCursor.value = result.next_cursor;
    } catch (failure) {
      if (version === generation) error.value = message(failure);
    } finally {
      if (version === generation) loading.value = false;
    }
  },
  { immediate: true },
);
onUnmounted(() => {
  generation++;
});
async function filter() {
  const query: Record<string, string> = {};
  for (const [key, v] of Object.entries({
    q: q.value.trim(),
    status: status.value,
    start: start.value,
    end: end.value,
  }))
    if (v) query[key] = v;
  const before = route.fullPath;
  await router.push({ query });
  if (route.fullPath === before) refreshing.value++;
}
function timestamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
</script>
<template>
  <nav class="breadcrumb" aria-label="当前位置">
    <RouterLink to="/">项目</RouterLink><span>/</span
    ><RouterLink :to="`/projects/${route.params.id}`">项目详情</RouterLink
    ><span>/</span><span>历史分析</span>
  </nav>
  <div class="page-heading project-heading">
    <div>
      <h1>{{ project?.name ?? "历史分析" }}</h1>
      <p class="muted">查找已保存的任务与报告</p>
    </div>
    <div class="section-actions">
      <RouterLink class="button" :to="`/projects/${route.params.id}`"
        >项目详情</RouterLink
      ><RouterLink class="button" :to="`/projects/${route.params.id}/analysis`"
        >分析与检索</RouterLink
      >
    </div>
  </div>
  <nav class="analysis-navigation stage-navigation" aria-label="智能分析导航">
    <RouterLink :to="`/projects/${route.params.id}/agent`">新建分析</RouterLink
    ><RouterLink
      :to="`/projects/${route.params.id}/agent/history`"
      aria-current="page"
      >历史分析</RouterLink
    >
  </nav>
  <section class="agent-panel">
    <form @submit.prevent="filter">
      <div class="history-filters">
        <label
          >关键词<input
            v-model="q"
            maxlength="200"
            placeholder="搜索问题或报告标题"
        /></label>
        <label
          >任务状态<SelectField
            v-model="status"
            :options="statuses"
            control-label="任务状态"
        /></label>
        <label>开始日期<input v-model="start" type="date" /></label
        ><label>结束日期<input v-model="end" type="date" /></label>
      </div>
      <div class="section-actions">
        <button class="primary" :disabled="loading">查询历史</button
        ><button
          type="button"
          :disabled="loading"
          @click="
            q = '';
            status = '';
            start = '';
            end = '';
            filter();
          "
        >
          清除筛选
        </button>
      </div>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted">正在查询历史…</p>
    <template v-else-if="!error"
      ><p class="muted">共 {{ total }} 个任务 · 时间按中国标准时间显示</p>
      <p v-if="!items.length" class="empty-state">没有符合条件的任务。</p>
      <ul class="history-list">
        <li v-for="run in items" :key="run.id">
          <RouterLink :to="`/projects/${route.params.id}/agent/runs/${run.id}`"
            ><strong>{{ run.report_title || run.question }}</strong>
            <p v-if="run.report_title">{{ run.question }}</p>
            <span class="muted small"
              >{{ runLabels[run.status] }} · {{ timestamp(run.created_at) }} ·
              {{ run.model }}</span
            ></RouterLink
          >
        </li>
      </ul>
      <div class="pagination">
        <button
          :disabled="!route.query.cursor"
          @click="router.push({ query: { ...route.query, cursor: undefined } })"
        >
          返回第一页</button
        ><button
          :disabled="!nextCursor"
          @click="
            router.push({
              query: { ...route.query, cursor: nextCursor ?? undefined },
            })
          "
        >
          下一页
        </button>
      </div>
    </template>
  </section>
</template>
