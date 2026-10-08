<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  activeRun,
  agentApi,
  runLabels,
  type AgentEvent,
  type AgentRun,
  type ReportSource,
} from "../agent";
import { api, message } from "../api";
import { analysisApi, type Citation } from "../analysis";
import SourcePreview from "../components/SourcePreview.vue";
import Report from "../components/agent/AgentReport.vue";
import AgentTimeline from "../components/agent/AgentTimeline.vue";
import { useAgentStream } from "../composables/useAgentStream";
import type { DataSource, Project } from "../types";
const route = useRoute(),
  router = useRouter();
const project = ref<Project | null>(null),
  sources = ref<DataSource[]>([]);
const run = ref<AgentRun | null>(null),
  events = ref<AgentEvent[]>([]);
const loading = ref(false),
  busy = ref(false),
  error = ref("");
const selected = ref<ReportSource | null>(null),
  citation = ref<Citation | null>(null);
const previewSection = ref<HTMLElement | null>(null);
let generation = 0,
  citing = 0,
  reading = 0;
let trigger: HTMLElement | null = null;
let scrollPosition = 0;
const source = computed(() =>
  sources.value.find((s) => s.id === selected.value?.source_id),
);
const panel = computed(() =>
  route.query.view === "process" ||
  (!run.value?.report && route.query.view !== "report")
    ? "process"
    : "report",
);
const streaming = useAgentStream(
  (value) => {
    reading++;
    if (run.value) run.value = { ...run.value, ...value };
  },
  (values) => {
    const seen = new Set(events.value.map((v) => v.sequence));
    events.value = [
      ...events.value,
      ...values.filter((v) => !seen.has(v.sequence)),
    ];
  },
  refresh,
);
async function eventHistory(p: string, id: string, version: number) {
  let after = 0;
  const result: AgentEvent[] = [];
  for (let page = 0; page < 50; page++) {
    const values = await agentApi.events(p, id, after);
    if (version !== generation) return [];
    result.push(...values);
    if (values.length < 100) return result;
    const next = values.at(-1)!.sequence;
    if (next <= after) throw new Error("执行事件顺序无效，请重新查询。");
    after = next;
  }
  throw new Error("执行记录超出展示上限，请联系维护者。");
}
async function refresh() {
  const p = String(route.params.id),
    id = String(route.params.runId),
    version = generation,
    request = ++reading;
  try {
    const [value, progress] = await Promise.all([
      agentApi.run(p, id),
      eventHistory(p, id, version),
    ]);
    if (version !== generation || request !== reading) return;
    run.value = value;
    events.value = progress;
    error.value = "";
    if (activeRun(value.status))
      streaming.start(p, id, progress.at(-1)?.sequence ?? 0);
  } catch (failure) {
    if (version === generation && request === reading)
      error.value = message(failure);
  }
}
watch(
  () => [route.params.id, route.params.runId],
  async () => {
    const version = ++generation;
    streaming.stop();
    citing++;
    selected.value = null;
    citation.value = null;
    project.value = null;
    run.value = null;
    events.value = [];
    error.value = "";
    busy.value = false;
    loading.value = true;
    const p = String(route.params.id);
    try {
      const [value, files] = await Promise.all([
        api.project(p),
        api.sources(p),
      ]);
      if (version !== generation) return;
      project.value = value;
      sources.value = files;
      await refresh();
    } catch (failure) {
      if (version === generation) error.value = message(failure);
    } finally {
      if (version === generation) loading.value = false;
    }
  },
  { immediate: true },
);
watch(panel, () => {
  citing++;
  selected.value = null;
  citation.value = null;
});
onUnmounted(() => {
  generation++;
  citing++;
});
async function cancel() {
  if (!run.value || busy.value) return;
  const version = generation,
    p = run.value.project_id,
    id = run.value.id;
  busy.value = true;
  // Close and invalidate the stream before a cancellation write can beat stale events.
  reading++;
  streaming.stop();
  try {
    const value = await agentApi.cancel(p, id);
    if (version === generation) run.value = value;
  } catch (failure) {
    if (version === generation) error.value = message(failure);
  } finally {
    if (version === generation) {
      busy.value = false;
      await refresh();
    }
  }
}
async function openEvidence(value: ReportSource, event: MouseEvent) {
  const version = generation,
    request = ++citing;
  trigger = event.currentTarget as HTMLElement;
  scrollPosition = window.scrollY;
  selected.value = value;
  citation.value = null;
  error.value = "";
  try {
    if (value.kind === "document" && value.chunk_id) {
      const result = await analysisApi.citation(
        String(route.params.id),
        value.chunk_id,
      );
      if (version !== generation || request !== citing) return;
      citation.value = result;
    }
    if (version !== generation || request !== citing) return;
    await nextTick();
    previewSection.value?.focus({ preventScroll: true });
    if (innerWidth <= 768)
      previewSection.value?.scrollIntoView({
        block: "start",
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
  } catch (failure) {
    if (version === generation && request === citing) {
      selected.value = null;
      error.value = message(failure);
    }
  }
}
async function closeEvidence() {
  citing++;
  selected.value = null;
  citation.value = null;
  await nextTick();
  if (trigger?.isConnected) trigger.focus({ preventScroll: true });
  window.scrollTo({ top: scrollPosition, behavior: "instant" });
}
async function reuse() {
  if (run.value)
    await router.push({
      path: `/projects/${run.value.project_id}/agent`,
      query: { from: run.value.id },
    });
}
</script>
<template>
  <nav class="breadcrumb" aria-label="当前位置">
    <RouterLink to="/">项目</RouterLink><span>/</span
    ><RouterLink :to="`/projects/${route.params.id}`">项目详情</RouterLink
    ><span>/</span
    ><RouterLink :to="`/projects/${route.params.id}/agent`">智能分析</RouterLink
    ><span>/</span><span>任务详情</span>
  </nav>
  <p v-if="loading" class="muted">正在加载任务…</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <template v-if="project && run">
    <div class="page-heading project-heading">
      <div>
        <h1>{{ project.name }}</h1>
        <p class="muted">任务详情</p>
      </div>
      <div class="section-actions">
        <RouterLink class="button" :to="`/projects/${project.id}`"
          >项目详情</RouterLink
        ><RouterLink
          class="button"
          :to="`/projects/${project.id}/agent/history`"
          >历史分析</RouterLink
        ><button @click="reuse">用此问题新建分析</button>
      </div>
    </div>
    <section class="agent-panel task-overview">
      <div class="section-heading">
        <p class="agent-run-status" role="status">
          {{ runLabels[run.status]
          }}<span v-if="activeRun(run.status)" class="muted small">
            · {{ streaming.connection.value }}</span
          >
        </p>
        <div class="section-actions">
          <button :disabled="busy" @click="refresh">刷新状态</button
          ><button
            v-if="activeRun(run.status)"
            :disabled="busy || run.cancel_requested"
            @click="cancel"
          >
            {{ run.cancel_requested ? "正在取消…" : "取消任务" }}
          </button>
        </div>
      </div>
      <p class="agent-question">{{ run.question }}</p>
      <p class="muted small">
        平台：{{ run.provider }} · 模型：{{ run.model }}
      </p>
      <p v-if="run.error" class="error" role="alert">{{ run.error }}</p>
    </section>
    <nav class="analysis-navigation stage-navigation" aria-label="任务内容">
      <RouterLink
        :to="{ query: { view: 'report' } }"
        :aria-current="panel === 'report' ? 'page' : undefined"
        >分析报告</RouterLink
      ><RouterLink
        :to="{ query: { view: 'process' } }"
        :aria-current="panel === 'process' ? 'page' : undefined"
        >执行过程</RouterLink
      >
    </nav>
    <div class="task-content" :class="{ 'has-evidence': !!selected }">
      <section
        class="agent-panel"
        :class="{ 'mobile-report-hidden': !!selected }"
      >
        <AgentTimeline v-if="panel === 'process'" :run="run" :events="events" />
        <Report
          v-else-if="run.report"
          :report="run.report"
          :selected-id="selected?.id"
          @evidence="openEvidence"
        />
        <p v-else class="muted">
          {{
            activeRun(run.status)
              ? "报告尚未完成，可查看执行过程。"
              : "此任务没有生成可发布的报告，请查看执行过程与错误提示。"
          }}
        </p>
      </section>
      <section
        v-if="selected"
        ref="previewSection"
        class="agent-panel agent-preview evidence-panel"
        tabindex="-1"
        aria-label="报告证据原文"
        @keydown.esc="closeEvidence"
      >
        <div class="section-heading">
          <h2>报告证据原文</h2>
          <button @click="closeEvidence">返回分析报告</button>
        </div>
        <p v-if="citation" class="evidence-text">{{ citation.text }}</p>
        <p
          v-if="selected.kind === 'csv' && (selected.records?.length ?? 0) > 1"
          class="muted small"
        >
          当前定位第一条引用记录（第
          {{ selected.records?.[0] }}
          条）；原文可翻页查看，统计使用全部符合条件的记录。
        </p>
        <p
          v-if="selected.kind === 'csv' && selected.records_truncated"
          class="notice"
        >
          仅定位引用样本；该统计使用全部
          {{ selected.record_count }} 条符合条件的记录。
        </p>
        <p v-if="selected.kind === 'document' && !citation" class="muted">
          正在读取引用…
        </p>
        <SourcePreview
          v-if="source && (selected.kind === 'csv' || citation)"
          :key="selected.id"
          :project-id="project.id"
          :source="source"
          :expected-hash="citation?.content_hash ?? selected.content_hash"
          :initial-page="
            citation?.page ??
            Math.ceil((citation?.record ?? selected.records?.[0] ?? 1) / 20)
          "
          :highlight="
            citation ?? {
              page: null,
              record: selected.records?.[0] ?? null,
              start: 0,
              end: 0,
              text: '',
            }
          "
          back-label="返回分析报告"
          @back="closeEvidence"
        />
        <p v-else-if="!source" class="notice">
          当前原数据源不可用，仍可阅读保存的报告与证据描述。
        </p>
      </section>
    </div>
  </template>
</template>
