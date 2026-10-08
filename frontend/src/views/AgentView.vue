<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  agentApi,
  runLabels,
  type ModelStatus,
  type RunSummary,
} from "../agent";
import { ApiError, api, dateLabel, message } from "../api";
import { analysisApi, type IndexVersion } from "../analysis";
import type { DataSource, Project } from "../types";
const route = useRoute(),
  router = useRouter();
const project = ref<Project | null>(null),
  sources = ref<DataSource[]>([]);
const configuration = ref<ModelStatus | null>(null),
  runs = ref<RunSummary[]>([]);
const indexes = ref<IndexVersion[]>([]),
  indexKnown = ref(false);
const question = ref(""),
  selection = ref<string[]>([]);
const password = ref("");
const loading = ref(false),
  busy = ref(false),
  uncertain = ref(false),
  error = ref("");
const storageNotice = ref("");
let requestId: string | null = null;
let generation = 0;
let quotaTimer: ReturnType<typeof setInterval> | undefined;
async function refreshConfiguration() {
  if (!project.value || busy.value || document.hidden) return;
  const version = generation,
    p = project.value.id;
  try {
    const value = await agentApi.configuration(p);
    if (version === generation) configuration.value = value;
  } catch {
    // 页面连接状态由全局标识反映，后端仍在每次提交与发送前校验真实额度。
  }
}
onMounted(() => {
  quotaTimer = setInterval(refreshConfiguration, 20_000);
  document.addEventListener("visibilitychange", refreshConfiguration);
});
const ready = computed(() => sources.value.filter((s) => s.status === "ready"));
const canSubmit = computed(
  () =>
    configuration.value?.configured &&
    configuration.value.enabled &&
    (!configuration.value.password_required || password.value.length > 0) &&
    (!configuration.value.quota || configuration.value.quota.can_start) &&
    question.value.trim() &&
    selection.value.length > 0 &&
    selection.value.length <= 20 &&
    !busy.value &&
    !uncertain.value,
);
function key() {
  return `newai:analysis-draft:${route.params.id}`;
}
function save() {
  if (!project.value) return;
  try {
    sessionStorage.setItem(
      key(),
      JSON.stringify({
        question: question.value,
        sourceIds: selection.value,
        requestId,
        uncertain: uncertain.value,
      }),
    );
  } catch {
    storageNotice.value =
      "浏览器无法暂存草稿，请保持本页打开，提交结果不确定时先确认原提交。";
  }
}
function restore() {
  try {
    const raw = sessionStorage.getItem(key());
    if (!raw) return false;
    const value = JSON.parse(raw);
    if (
      typeof value.question !== "string" ||
      value.question.length > 2000 ||
      !Array.isArray(value.sourceIds) ||
      value.sourceIds.length > 20 ||
      !value.sourceIds.every((v: unknown) => typeof v === "string") ||
      (value.requestId != null &&
        (typeof value.requestId !== "string" ||
          !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
            value.requestId,
          )))
    )
      throw new Error();
    question.value = value.question;
    selection.value = value.sourceIds;
    requestId = value.requestId ?? null;
    uncertain.value = !!value.uncertain && !!requestId;
    // Reloading during an in-flight submission must preserve its original intention.
    if (requestId) uncertain.value = true;
    return true;
  } catch {
    storageNotice.value = "草稿暂存不可用或内容无效，未自动恢复。";
    return false;
  }
}
watch([question, selection], save, { deep: true });
watch(
  () => [route.params.id, route.query.from],
  async () => {
    const version = ++generation,
      p = String(route.params.id);
    project.value = null;
    question.value = "";
    password.value = "";
    selection.value = [];
    requestId = null;
    uncertain.value = false;
    busy.value = false;
    error.value = "";
    storageNotice.value = "";
    loading.value = true;
    indexKnown.value = false;
    indexes.value = [];
    try {
      const [value, files, config, history] = await Promise.all([
        api.project(p),
        api.sources(p),
        agentApi.configuration(p),
        agentApi.list(p),
      ]);
      if (version !== generation) return;
      sources.value = files;
      configuration.value = config;
      runs.value = history;
      const restored = restore();
      if (!restored)
        selection.value = files
          .filter((s) => s.status === "ready")
          .slice(0, 20)
          .map((s) => s.id);
      else if (!uncertain.value)
        selection.value = selection.value.filter((id) =>
          files.some((s) => s.id === id && s.status === "ready"),
        );
      if (typeof route.query.from === "string" && !uncertain.value) {
        const old = await agentApi.run(p, route.query.from);
        if (version !== generation) return;
        question.value = old.question;
        selection.value = old.snapshot.sources
          .map((s) => s.id)
          .filter((id) =>
            files.some((s) => s.id === id && s.status === "ready"),
          );
      }
      project.value = value;
      save();
      void analysisApi
        .indexes(p)
        .then((values) => {
          if (version === generation) {
            indexes.value = values;
            indexKnown.value = true;
          }
        })
        .catch(() => {});
    } catch (failure) {
      if (version === generation) error.value = message(failure);
    } finally {
      if (version === generation) loading.value = false;
    }
  },
  { immediate: true },
);
onUnmounted(() => {
  clearInterval(quotaTimer);
  document.removeEventListener("visibilitychange", refreshConfiguration);
  password.value = "";
  generation++;
});
async function submit(confirm = false) {
  if (!project.value || busy.value || (!confirm && !canSubmit.value)) return;
  const p = project.value.id,
    version = generation;
  busy.value = true;
  error.value = "";
  requestId ??= crypto.randomUUID();
  save();
  try {
    const run = await agentApi.create(
      p,
      question.value.trim(),
      selection.value,
      requestId,
      password.value,
    );
    if (version !== generation) return;
    requestId = null;
    uncertain.value = false;
    save();
    await router.push(`/projects/${p}/agent/runs/${run.id}`);
  } catch (failure) {
    if (version === generation) {
      uncertain.value = !(
        failure instanceof ApiError &&
        [400, 401, 403, 404, 409, 422, 429, 503].includes(failure.status)
      );
      if (!uncertain.value) requestId = null;
      error.value = uncertain.value
        ? `${message(failure)} 请先确认原提交。`
        : message(failure);
      save();
    }
  } finally {
    if (version === generation) {
      busy.value = false;
      password.value = "";
    }
  }
}
async function refresh() {
  if (!project.value || busy.value) return;
  const p = project.value.id,
    version = generation;
  busy.value = true;
  error.value = "";
  try {
    const [history, config] = await Promise.all([
      agentApi.list(p),
      agentApi.configuration(p),
    ]);
    if (version !== generation) return;
    runs.value = history;
    configuration.value = config;
    const accepted =
      uncertain.value && history.find((r) => r.request_id === requestId);
    if (accepted) {
      requestId = null;
      uncertain.value = false;
      save();
      await router.push(`/projects/${p}/agent/runs/${accepted.id}`);
    }
  } catch (failure) {
    if (version === generation) error.value = message(failure);
  } finally {
    if (version === generation) busy.value = false;
  }
}
function clear() {
  question.value = "";
  selection.value = [];
  save();
}
function readiness(source: DataSource) {
  if (source.status !== "ready") return "文件尚未就绪";
  if (!indexKnown.value)
    return source.kind === "csv" ? "可使用表格工具" : "索引状态待核对";
  return indexes.value.some(
    (v) => v.source_id === source.id && v.active && v.status === "ready",
  )
    ? "可检索证据"
    : source.kind === "csv"
      ? "可使用表格工具"
      : "尚无启用索引";
}
</script>
<template>
  <nav class="breadcrumb" aria-label="当前位置">
    <RouterLink to="/">项目</RouterLink><span>/</span
    ><RouterLink :to="`/projects/${route.params.id}`">项目详情</RouterLink
    ><span>/</span><span>智能分析</span>
  </nav>
  <p v-if="loading" class="muted">正在加载智能分析…</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <template v-if="project">
    <div class="page-heading project-heading">
      <div>
        <h1>{{ project.name }}</h1>
        <p class="muted">智能分析</p>
      </div>
      <div class="section-actions">
        <RouterLink class="button" :to="`/projects/${project.id}`"
          >项目详情</RouterLink
        ><RouterLink class="button" :to="`/projects/${project.id}/analysis`"
          >分析与检索</RouterLink
        >
      </div>
    </div>
    <nav class="analysis-navigation stage-navigation" aria-label="智能分析导航">
      <RouterLink :to="`/projects/${project.id}/agent`" aria-current="page"
        >新建分析</RouterLink
      ><RouterLink :to="`/projects/${project.id}/agent/history`"
        >历史分析</RouterLink
      >
    </nav>
    <section
      class="agent-panel agent-create"
      aria-labelledby="agent-input-heading"
    >
      <h2 id="agent-input-heading">提出问题</h2>
      <p class="muted">说明指标、日期及比较范围；分析仅使用选中的数据源。</p>
      <p v-if="configuration" class="notice">
        {{ configuration.notice
        }}<template v-if="!configuration.enabled">
          真实模型调用已关闭，当前无法提交新分析。</template
        >
      </p>
      <p v-if="configuration?.configured" class="small">
        平台：{{ configuration.provider }} · 模型：{{ configuration.model }}
      </p>
      <p v-if="storageNotice" class="notice">{{ storageNotice }}</p>
      <p v-if="configuration?.quota" class="notice quota-status" role="status">
        本小时可用 {{ configuration.quota.remaining.toLocaleString("zh-CN") }} /
        {{ configuration.quota.limit.toLocaleString("zh-CN") }} Tokens ·
        {{ dateLabel(configuration.quota.reset_at) }} 重置（中国标准时间）
        <span v-if="configuration.quota.unknown_requests" class="small"
          >部分请求未返回用量，已按保守预留计入。</span
        >
      </p>
      <form @submit.prevent="submit()">
        <fieldset :disabled="busy || uncertain">
          <label
            >分析问题<textarea
              v-model="question"
              required
              maxlength="2000"
              rows="5"
              placeholder="例如：比较九月与八月的净销售额，按地区分组，并检索客户反馈。请区分事实与推断。"
            />
          </label>
          <fieldset class="agent-source-options">
            <legend>数据源（最多20个）</legend>
            <label
              v-for="source in sources"
              :key="source.id"
              class="agent-source-option"
              ><input
                v-model="selection"
                type="checkbox"
                :value="source.id"
                :disabled="source.status !== 'ready'"
              /><span
                >{{ source.filename
                }}<small class="muted"
                  >{{ source.kind === "csv" ? "表格" : "文档" }} ·
                  {{ readiness(source) }}</small
                ></span
              ></label
            >
            <p v-if="!ready.length" class="muted">
              尚无已就绪的数据源，请先到项目详情导入文件。
            </p>
          </fieldset>
        </fieldset>
        <label v-if="configuration?.password_required" class="call-password">
          调用密码
          <input
            v-model="password"
            type="password"
            maxlength="128"
            autocomplete="off"
            :disabled="busy"
          />
          <span class="small muted">每次新建分析输入一次，提交后清空。</span>
        </label>
        <div class="section-actions">
          <button class="primary" :disabled="!canSubmit">
            {{ busy ? "正在提交…" : "开始分析" }}</button
          ><button type="button" :disabled="busy || uncertain" @click="clear">
            清空草稿
          </button>
        </div>
      </form>
      <div v-if="uncertain" class="agent-actions">
        <p class="notice">
          提交结果尚未确认，保留原问题、范围和请求编号，刷新后仍可确认。
        </p>
        <button :disabled="busy" @click="submit(true)">确认原提交</button>
      </div>
    </section>
    <section class="agent-panel recent-runs">
      <div class="section-heading">
        <h2>最近任务</h2>
        <button :disabled="busy" @click="refresh">刷新状态</button>
      </div>
      <p v-if="!runs.length" class="muted">暂无分析任务。</p>
      <ul class="history-list">
        <li v-for="run in runs.slice(0, 5)" :key="run.id">
          <RouterLink :to="`/projects/${project.id}/agent/runs/${run.id}`"
            ><strong>{{ run.question }}</strong
            ><span class="muted small"
              >{{ runLabels[run.status] }} ·
              {{ dateLabel(run.created_at) }}</span
            ></RouterLink
          >
        </li>
      </ul>
      <RouterLink :to="`/projects/${project.id}/agent/history`"
        >查看全部历史分析</RouterLink
      >
    </section>
  </template>
</template>
