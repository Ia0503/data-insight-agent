<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, dateLabel, message } from "../api";
import {
  analysisApi,
  type Citation,
  type IndexVersion,
  type MappingResult,
  type MetricResult,
  type ToolResult,
} from "../analysis";
import type { DataSource, Project } from "../types";
import SourcePreview from "../components/SourcePreview.vue";
import SelectField from "../components/SelectField.vue";

const route = useRoute();
const router = useRouter();
const tabs = [
  { key: "tasks", label: "分析任务" },
  { key: "search", label: "证据检索" },
  { key: "index", label: "文档索引" },
];
const activeTab = computed(() =>
  tabs.some((tab) => tab.key === route.query.tab)
    ? String(route.query.tab)
    : "tasks",
);
function selectTab(key: string) {
  void router.push({ query: { ...route.query, tab: key } });
}
async function tabKeydown(event: KeyboardEvent, index: number) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
          tabs.length;
  await router.push({ query: { ...route.query, tab: tabs[next]!.key } });
  await nextTick();
  document.getElementById(`analysis-tab-${tabs[next]!.key}`)?.focus();
}
const project = ref<Project | null>(null);
const sources = ref<DataSource[]>([]);
const indexes = ref<IndexVersion[]>([]);
const model = ref<Awaited<ReturnType<typeof analysisApi.model>> | null>(null);
const loading = ref(false),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const csvId = ref(""),
  indexSourceId = ref("");
const fields = ref<Record<string, string>>({});
const mappingResult = ref<MappingResult | null>(null);
const mappingSaved = ref(false);
const mappingLoading = ref(false);
// 编辑后的映射必须重新校验保存，避免界面新选择与服务端旧映射混用。
watch(
  fields,
  () => {
    mappingSaved.value = false;
    metricResult.value = null;
  },
  { deep: true, flush: "sync" },
);
const metricResult = ref<MetricResult | null>(null);
const metric = ref("net_sales"),
  start = ref("2026-09-01"),
  end = ref("2026-09-30"),
  region = ref(""),
  product = ref(""),
  group = ref("region"),
  compare = ref(true);
const textField = ref(""),
  recordIdField = ref(""),
  force = ref(false);
const query = ref(""),
  searchSourceId = ref(""),
  minSimilarity = ref(0.35);
const hits = ref<Citation[]>([]),
  searched = ref(false),
  searchNotice = ref("");
const citation = ref<Citation | null>(null),
  citationSection = ref<HTMLElement | null>(null);
const tool = ref("filter_data"),
  filterField = ref(""),
  filterValue = ref(""),
  toolGroup = ref(""),
  toolValue = ref(""),
  aggregation = ref("count");
const toolResult = ref<ToolResult | null>(null);
const fieldLabels: Record<string, string> = {
  order_id: "订单编号",
  paid_at: "支付日期",
  paid_amount: "实付金额（元）",
  refund_amount: "累计退款（元）",
  region: "地区",
  product: "产品",
  status: "订单状态",
};
const metricLabels: Record<string, string> = {
  paid_sales: "实付总额",
  net_sales: "净销售额",
  paid_orders: "已支付订单数",
  refund_rate: "退款订单比例",
};
const states = {
  queued: "排队中",
  processing: "建立中",
  ready: "已就绪",
  failed: "失败",
};
const readySources = computed(() =>
  sources.value.filter((s) => s.status === "ready"),
);
const csvSources = computed(() =>
  readySources.value.filter((s) => s.kind === "csv"),
);
const csvSource = computed(() =>
  sources.value.find((s) => s.id === csvId.value),
);
const indexSource = computed(() =>
  sources.value.find((s) => s.id === indexSourceId.value),
);
const columns = computed(
  () => csvSource.value?.metadata_json.columns?.map((c) => c.name) ?? [],
);
const indexColumns = computed(
  () => indexSource.value?.metadata_json.columns?.map((c) => c.name) ?? [],
);
const citationSource = computed(() =>
  sources.value.find((s) => s.id === citation.value?.source_id),
);
const citationPage = computed(
  () => citation.value?.page ?? Math.ceil((citation.value?.record ?? 1) / 20),
);
let generation = 0,
  csvGeneration = 0,
  refreshGeneration = 0,
  searchGeneration = 0,
  retrieving = false,
  polling = false;
function clearSearch() {
  // 激活版本变化后，旧结果和在途检索/引用响应必须一起失效。
  searchGeneration++;
  citation.value = null;
  hits.value = [];
  searched.value = false;
  searchNotice.value = "";
}
function activeIndexes(versions: IndexVersion[]) {
  return versions
    .filter((index) => index.active)
    .map((index) => index.id)
    .sort()
    .join(",");
}
const timer = setInterval(() => {
  if (
    indexes.value.some(
      (i) => i.status === "queued" || i.status === "processing",
    )
  )
    void refreshIndexes();
}, 2000);

watch(
  () => route.params.id,
  async (id) => {
    const current = ++generation;
    csvGeneration++;
    project.value = null;
    sources.value = [];
    indexes.value = [];
    model.value = null;
    csvId.value = "";
    indexSourceId.value = "";
    searchSourceId.value = "";
    query.value = "";
    metricResult.value = null;
    mappingResult.value = null;
    toolResult.value = null;
    clearSearch();
    retrieving = false;
    error.value = "";
    notice.value = "";
    busy.value = false;
    loading.value = true;
    try {
      const [p, files, versions, m] = await Promise.all([
        api.project(String(id)),
        api.sources(String(id)),
        analysisApi.indexes(String(id)),
        analysisApi.model(String(id)),
      ]);
      if (current !== generation) return;
      project.value = p;
      sources.value = files;
      indexes.value = versions;
      model.value = m;
      csvId.value =
        files.find((s) => s.kind === "csv" && s.status === "ready")?.id ?? "";
      indexSourceId.value =
        files.find((s) => s.kind === "pdf" && s.status === "ready")?.id ??
        readySources.value[0]?.id ??
        "";
    } catch (e) {
      if (current === generation) error.value = message(e);
    } finally {
      if (current === generation) loading.value = false;
    }
  },
  { immediate: true },
);
watch(csvId, async (id) => {
  const current = generation,
    request = ++csvGeneration;
  fields.value = {};
  mappingResult.value = null;
  metricResult.value = null;
  toolResult.value = null;
  filterField.value = "";
  filterValue.value = "";
  toolGroup.value = "";
  toolValue.value = "";
  mappingLoading.value = false;
  if (!id || !project.value) return;
  mappingLoading.value = true;
  try {
    const result = await analysisApi.mapping(project.value.id, id);
    if (current === generation && request === csvGeneration) {
      fields.value = result.fields;
      mappingSaved.value = Object.keys(result.fields).length === 7;
    }
  } catch (e) {
    if (current === generation && request === csvGeneration)
      error.value = message(e);
  } finally {
    if (current === generation && request === csvGeneration)
      mappingLoading.value = false;
  }
});
watch(indexSourceId, () => {
  textField.value = "";
  recordIdField.value = "";
  force.value = false;
});
onUnmounted(() => {
  generation++;
  csvGeneration++;
  clearInterval(timer);
});

async function perform(action: (id: string, current: number) => Promise<void>) {
  if (!project.value || busy.value) return;
  const current = generation,
    id = project.value.id;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action(id, current);
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function refreshIndexes() {
  if (!project.value || polling) return;
  const current = generation,
    request = ++refreshGeneration,
    id = project.value.id;
  polling = true;
  try {
    const result = await analysisApi.indexes(id);
    if (current === generation && request === refreshGeneration) {
      if (activeIndexes(indexes.value) !== activeIndexes(result)) {
        const stale = searched.value || citation.value || retrieving;
        clearSearch();
        if (stale) notice.value = "已启用的索引版本发生变化，请重新检索。";
      }
      indexes.value = result;
    }
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    polling = false;
  }
}
function saveMapping() {
  void perform(async (id, current) => {
    const source = csvId.value;
    const result = await analysisApi.saveMapping(id, source, {
      ...fields.value,
    });
    if (current !== generation || csvId.value !== source) return;
    mappingResult.value = result;
    mappingSaved.value = result.valid;
    metricResult.value = null;
    notice.value = result.valid
      ? "字段映射已校验并保存。"
      : "校验未通过，旧映射未覆盖。";
  });
}
function calculate() {
  void perform(async (id, current) => {
    const source = csvId.value;
    const result = await analysisApi.metric(id, source, {
      metric: metric.value,
      start: start.value,
      end: end.value,
      region: region.value || null,
      product: product.value || null,
      group: group.value || null,
      compare_previous: compare.value,
    });
    if (current === generation && csvId.value === source)
      metricResult.value = result;
  });
}
function buildIndex() {
  void perform(async (id, current) => {
    await analysisApi.build(id, indexSourceId.value, {
      text_field: textField.value || null,
      record_id_field: recordIdField.value || null,
      force: force.value,
    });
    if (current === generation) {
      notice.value = "索引任务已提交，状态会自动更新。";
      await refreshIndexes();
    }
  });
}
function activateIndex(index: IndexVersion) {
  void perform(async (id, current) => {
    const activated = await analysisApi.activate(id, index.id);
    if (current === generation) {
      // 以写入确认作为当前版本，忽略切换前发出的轮询；即使刷新仍在途也能更新导航状态。
      refreshGeneration++;
      indexes.value = indexes.value.map((version) =>
        version.source_id !== activated.source_id
          ? version
          : version.id === activated.id
            ? activated
            : { ...version, active: false },
      );
      clearSearch();
      notice.value = "索引版本已切换，请重新检索。";
      await refreshIndexes();
    }
  });
}
function retrieve() {
  void perform(async (id, current) => {
    clearSearch();
    const request = searchGeneration;
    retrieving = true;
    try {
      const result = await analysisApi.search(id, {
        query: query.value,
        source_ids: searchSourceId.value ? [searchSourceId.value] : [],
        top_k: 5,
        min_similarity: minSimilarity.value,
      });
      if (current === generation && request === searchGeneration) {
        hits.value = result.results;
        searched.value = true;
        searchNotice.value = result.reason ?? result.notice ?? "";
      }
    } catch (e) {
      if (current === generation && request === searchGeneration) throw e;
    } finally {
      if (current === generation) retrieving = false;
    }
  });
}
function openCitation(hit: Citation) {
  void perform(async (id, current) => {
    const request = searchGeneration;
    const result = await analysisApi.citation(id, hit.chunk_id);
    if (current !== generation || request !== searchGeneration) return;
    citation.value = result;
    await nextTick();
    citationSection.value?.focus({ preventScroll: true });
    citationSection.value?.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  });
}
async function closeCitation() {
  const chunk = citation.value?.chunk_id;
  citation.value = null;
  await nextTick();
  if (chunk)
    document
      .querySelector<HTMLButtonElement>(
        `[data-citation="${CSS.escape(chunk)}"]`,
      )
      ?.focus();
}
function runTool() {
  void perform(async (id, current) => {
    const source = csvId.value;
    if (
      (filterField.value && !columns.value.includes(filterField.value)) ||
      (tool.value === "group_by" &&
        (!columns.value.includes(toolGroup.value) ||
          (aggregation.value !== "count" &&
            !columns.value.includes(toolValue.value))))
    )
      throw new Error("筛选、分组或聚合列已失效，请从当前 CSV 重新选择。");
    const result = await analysisApi.tool(id, source, {
      tool: tool.value,
      filters: filterField.value
        ? [
            {
              field: filterField.value,
              operator: "eq",
              value: filterValue.value,
            },
          ]
        : [],
      group: toolGroup.value || null,
      value: toolValue.value || null,
      aggregation: aggregation.value,
      limit: 50,
    });
    if (current === generation && csvId.value === source)
      toolResult.value = result;
  });
}
</script>

<template>
  <nav class="breadcrumb" aria-label="当前位置">
    <RouterLink to="/">项目</RouterLink><span>/</span
    ><RouterLink :to="`/projects/${route.params.id}`">项目详情</RouterLink
    ><span>/</span><span>分析与检索</span>
  </nav>
  <p v-if="loading" class="muted">正在加载分析工作区…</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <p v-if="notice" class="notice" role="status">{{ notice }}</p>
  <template v-if="project">
    <div class="page-heading project-heading">
      <div>
        <h1>{{ project.name }}</h1>
        <p class="muted">分析与检索</p>
      </div>
      <div class="section-actions">
        <RouterLink class="button" :to="`/projects/${project.id}`"
          >项目详情</RouterLink
        >
        <RouterLink class="button primary" :to="`/projects/${project.id}/agent`"
          >智能分析</RouterLink
        >
      </div>
    </div>
    <nav class="analysis-navigation" role="tablist" aria-label="分析任务导航">
      <button
        v-for="(tab, index) in tabs"
        :id="`analysis-tab-${tab.key}`"
        :key="tab.key"
        type="button"
        role="tab"
        :aria-selected="activeTab === tab.key"
        :aria-controls="`analysis-panel-${tab.key}`"
        :tabindex="activeTab === tab.key ? 0 : -1"
        @click="selectTab(tab.key)"
        @keydown="tabKeydown($event, index)"
      >
        {{ tab.label }}
      </button>
    </nav>
    <section
      v-show="activeTab === 'tasks'"
      id="analysis-panel-tasks"
      class="analysis-panel"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="analysis-tab-tasks"
    >
      <h2 id="metric-heading">业务指标</h2>
      <p class="muted small">
        订单粒度：每条记录一笔订单。状态使用 paid / refunded /
        cancelled；金额为人民币元。取消订单排除，退款按支付日期归属。
      </p>
      <fieldset :disabled="busy || mappingLoading">
        <label
          >订单数据源<SelectField
            v-model="csvId"
            control-label="订单数据源"
            :options="[
              { value: '', label: '请选择 CSV' },
              ...csvSources.map((s) => ({ value: s.id, label: s.filename })),
            ]"
            :disabled="busy || mappingLoading"
        /></label>
        <template v-if="csvId">
          <details class="analysis-details" open>
            <summary>业务字段映射</summary>
            <form @submit.prevent="saveMapping">
              <div class="analysis-grid">
                <label v-for="(label, key) in fieldLabels" :key="key"
                  >{{ label
                  }}<SelectField
                    v-model="fields[key]"
                    :control-label="label"
                    :options="[
                      { value: '', label: '请选择列' },
                      ...columns.map((c) => ({ value: c, label: c })),
                    ]"
                    :disabled="busy || mappingLoading"
                    required
                /></label>
              </div>
              <button class="primary">校验并保存映射</button>
            </form>
          </details>
          <div v-if="mappingResult" role="status">
            <p>
              {{ mappingResult.valid ? "校验通过" : "校验未通过" }}：{{
                mappingResult.row_count
              }}
              条记录，{{ mappingResult.issue_count }} 项问题。
            </p>
            <ul v-if="mappingResult.issues.length">
              <li v-for="(issue, i) in mappingResult.issues" :key="i">
                记录 {{ issue.record }}：{{ issue.message }}
              </li>
            </ul>
            <p v-if="mappingResult.issues_truncated">
              仅展示前 20 项，请修正后重新校验。
            </p>
          </div>
          <form @submit.prevent="calculate">
            <div class="analysis-grid">
              <label
                >指标<SelectField
                  v-model="metric"
                  control-label="指标"
                  :options="
                    Object.entries(metricLabels).map(([value, label]) => ({
                      value,
                      label,
                    }))
                  "
                  :disabled="busy || mappingLoading" /></label
              ><label
                >开始日期<input v-model="start" type="date" required /></label
              ><label
                >结束日期<input
                  v-model="end"
                  type="date"
                  required
                  :min="start" /></label
              ><label
                >地区筛选<input
                  v-model="region"
                  placeholder="留空表示全部"
                  maxlength="120" /></label
              ><label
                >产品筛选<input
                  v-model="product"
                  placeholder="留空表示全部"
                  maxlength="120" /></label
              ><label
                >分组<SelectField
                  v-model="group"
                  control-label="分组"
                  :options="[
                    { value: '', label: '不分组' },
                    { value: 'month', label: '月份' },
                    { value: 'region', label: '地区' },
                    { value: 'product', label: '产品' },
                  ]"
                  :disabled="busy || mappingLoading"
              /></label>
            </div>
            <label class="check-label"
              ><input
                v-model="compare"
                type="checkbox"
              />比较上一期（自然月或等天数）</label
            ><button class="primary" :disabled="!mappingSaved">计算指标</button>
          </form>
        </template>
      </fieldset>
      <div v-if="metricResult" class="analysis-result" role="status">
        <p>
          {{ metricLabels[metricResult.metric] }} ·
          {{ metricResult.filters.start }} 至 {{ metricResult.filters.end }} ·
          {{ metricResult.filters.region ?? "全部地区" }} ·
          {{ metricResult.filters.product ?? "全部产品" }}
        </p>
        <p class="metric-number">
          {{ metricResult.value ?? "无定义" }}
          <span>{{ metricResult.unit }}</span>
        </p>
        <p v-if="metricResult.comparison">
          基期 {{ metricResult.comparison.start }} 至
          {{ metricResult.comparison.end }}：{{
            metricResult.comparison.value ?? "无定义"
          }}
          {{ metricResult.unit }}；变化
          {{
            metricResult.comparison.change_percent !== null
              ? metricResult.comparison.change_percent + "%"
              : "无定义"
          }}。{{ metricResult.comparison.reason }}
        </p>
        <div v-if="metricResult.groups.length" class="table-scroll">
          <table aria-label="指标分组结果">
            <thead>
              <tr>
                <th>分组</th>
                <th>数值</th>
                <th>贡献比例</th>
                <th>订单记录数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="g in metricResult.groups" :key="g.group">
                <td>{{ g.group }}</td>
                <td>{{ g.value ?? "无定义" }}</td>
                <td>
                  {{
                    g.contribution_percent !== null
                      ? g.contribution_percent + "%"
                      : "—"
                  }}
                </td>
                <td>{{ g.record_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p
          v-for="warning in metricResult.warnings"
          :key="warning"
          class="muted small"
        >
          {{ warning }}
        </p>
        <details>
          <summary>计算依据与版本</summary>
          <p>
            {{ metricResult.evidence.filename }} ·
            {{ metricResult.evidence.record_count }} 条参与记录
          </p>
          <p>
            记录序号：{{ metricResult.evidence.records.join("、") || "无"
            }}{{
              metricResult.evidence.records_truncated ? "（仅列前100条）" : ""
            }}
          </p>
          <p class="small break-text">
            {{ metricResult.evidence.locator }} 文件 SHA-256：{{
              metricResult.evidence.content_hash
            }}；指标 {{ metricResult.metric_version }}；映射
            {{ metricResult.mapping_version }}
          </p>
        </details>
      </div>
      <details v-if="csvId" class="analysis-details">
        <summary>通用筛选与分组工具</summary>
        <form @submit.prevent="runTool">
          <fieldset :disabled="busy">
            <div class="analysis-grid">
              <label
                >工具<SelectField
                  v-model="tool"
                  control-label="工具"
                  :options="[
                    { value: 'filter_data', label: '筛选记录' },
                    { value: 'group_by', label: '分组聚合' },
                  ]"
                  :disabled="busy" /></label
              ><label
                >等值筛选列<SelectField
                  v-model="filterField"
                  control-label="等值筛选列"
                  :options="[
                    { value: '', label: '不筛选' },
                    ...columns.map((c) => ({ value: c, label: c })),
                  ]"
                  :disabled="busy" /></label
              ><label
                >筛选值<input v-model="filterValue" maxlength="200" /></label
              ><template v-if="tool === 'group_by'"
                ><label
                  >分组列<SelectField
                    v-model="toolGroup"
                    control-label="分组列"
                    :options="[
                      { value: '', label: '请选择' },
                      ...columns.map((c) => ({ value: c, label: c })),
                    ]"
                    :disabled="busy"
                    required /></label
                ><label
                  >聚合<SelectField
                    v-model="aggregation"
                    control-label="聚合"
                    :options="[
                      { value: 'count', label: '计数' },
                      { value: 'sum', label: '求和' },
                      { value: 'mean', label: '平均值' },
                      { value: 'min', label: '最小值' },
                      { value: 'max', label: '最大值' },
                    ]"
                    :disabled="busy" /></label
                ><label v-if="aggregation !== 'count'"
                  >数值列<SelectField
                    v-model="toolValue"
                    control-label="数值列"
                    :options="[
                      { value: '', label: '请选择' },
                      ...columns.map((c) => ({ value: c, label: c })),
                    ]"
                    :disabled="busy"
                    required /></label
              ></template>
            </div>
            <button>运行工具</button>
          </fieldset>
        </form>
        <div v-if="toolResult" class="table-scroll">
          <table aria-label="通用工具结果">
            <thead>
              <tr>
                <th>{{ toolResult.rows ? "记录序号" : "分组" }}</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in toolResult.rows" :key="row.record">
                <td>{{ row.record }}</td>
                <td>
                  {{
                    Object.entries(row.values)
                      .map(([k, v]) => `${k}：${v}`)
                      .join("；")
                  }}
                </td>
              </tr>
              <tr v-for="g in toolResult.groups" :key="g.group">
                <td>{{ g.group }}</td>
                <td>{{ g.value }}（{{ g.record_count }}条）</td>
              </tr>
            </tbody>
          </table>
          <p v-if="toolResult.truncated">结果仅展示前50项，请缩小筛选范围。</p>
          <p v-else-if="!toolResult.rows?.length && !toolResult.groups?.length">
            没有匹配结果。
          </p>
        </div>
      </details>
    </section>
    <section
      v-show="activeTab === 'index'"
      id="analysis-panel-index"
      class="analysis-panel"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="analysis-tab-index"
    >
      <div class="section-heading">
        <h2 id="index-heading">文档与反馈索引</h2>
        <button :disabled="busy" @click="refreshIndexes">刷新索引</button>
      </div>
      <p class="muted small break-text">
        模型 {{ model?.profile.model }} · {{ model?.profile.dimension }} 维 ·
        {{
          model?.downloaded ? "本地文件已准备" : "本地模型尚未准备"
        }}。文件解析和索引分别处理，旧索引在新版本成功后才切换。
      </p>
      <form @submit.prevent="buildIndex">
        <fieldset :disabled="busy">
          <div class="analysis-grid">
            <label
              >索引数据源<SelectField
                v-model="indexSourceId"
                control-label="索引数据源"
                :options="[
                  { value: '', label: '请选择' },
                  ...readySources.map((s) => ({
                    value: s.id,
                    label: s.filename,
                  })),
                ]"
                :disabled="busy"
                required /></label
            ><template v-if="indexSource?.kind === 'csv'"
              ><label
                >反馈文本列<SelectField
                  v-model="textField"
                  control-label="反馈文本列"
                  :options="[
                    { value: '', label: '请选择' },
                    ...indexColumns.map((c) => ({ value: c, label: c })),
                  ]"
                  :disabled="busy"
                  required /></label
              ><label
                >反馈编号列<SelectField
                  v-model="recordIdField"
                  control-label="反馈编号列"
                  :options="[
                    { value: '', label: '使用记录序号' },
                    ...indexColumns.map((c) => ({ value: c, label: c })),
                  ]"
                  :disabled="busy" /></label
            ></template>
          </div>
          <label class="check-label"
            ><input
              v-model="force"
              type="checkbox"
            />强制建立新版本（保留旧索引）</label
          ><button
            class="primary"
            :disabled="!indexSourceId || !model?.downloaded"
          >
            建立索引
          </button>
        </fieldset>
      </form>
      <ul class="index-list">
        <li v-for="index in indexes" :key="index.id">
          <div>
            <strong>{{
              sources.find((s) => s.id === index.source_id)?.filename ??
              "数据源"
            }}</strong>
            <p class="small">
              {{ states[index.status] }} · {{ index.chunk_count }} 个片段 ·
              {{ index.active ? "当前启用" : "未启用" }} ·
              {{ dateLabel(index.created_at) }}
            </p>
            <p class="small break-text">
              {{ index.profile.model }} · {{ index.id }}
            </p>
            <p v-if="index.error" class="error">{{ index.error }}</p>
          </div>
          <button
            v-if="index.status === 'ready' && !index.active"
            :disabled="busy"
            @click="activateIndex(index)"
          >
            启用此版本
          </button>
        </li>
      </ul>
      <p v-if="!indexes.length" class="muted">
        尚无索引。PDF 按页定位，反馈 CSV 按数据记录序号定位。
      </p>
    </section>
    <section
      v-show="activeTab === 'search'"
      id="analysis-panel-search"
      class="analysis-panel"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="analysis-tab-search"
    >
      <h2 id="search-heading">证据检索</h2>
      <form @submit.prevent="retrieve">
        <fieldset :disabled="busy">
          <label
            >检索问题<textarea
              v-model="query"
              required
              maxlength="1000"
              placeholder="例如：用户退款是否提到新版本崩溃？"
            ></textarea>
          </label>
          <div class="analysis-grid search-controls">
            <label
              >检索范围<SelectField
                v-model="searchSourceId"
                control-label="检索范围"
                :options="[
                  { value: '', label: '当前项目全部已索引文件' },
                  ...readySources.map((s) => ({
                    value: s.id,
                    label: s.filename,
                  })),
                ]"
                :disabled="busy" /></label
            ><label
              >最低相似度<input
                v-model.number="minSimilarity"
                type="number"
                min="-1"
                max="1"
                step="0.01"
                required
            /></label>
          </div>
          <button class="primary">检索证据</button>
        </fieldset>
      </form>
      <p v-if="searched" role="status" class="muted">{{ searchNotice }}</p>
      <ol class="search-results">
        <li
          v-for="hit in hits"
          :key="hit.chunk_id"
          :class="{ 'selected-evidence': citation?.chunk_id === hit.chunk_id }"
        >
          <div class="section-heading">
            <strong
              >{{ hit.filename }} ·
              {{ hit.page ? `第${hit.page}页` : `记录${hit.record}` }}</strong
            ><span class="similarity-score"
              >相似度 {{ hit.similarity?.toFixed(3) }}</span
            >
          </div>
          <p class="evidence-text">{{ hit.text }}</p>
          <button
            :disabled="busy"
            :data-citation="hit.chunk_id"
            @click="openCitation(hit)"
          >
            查看引用原文
          </button>
        </li>
      </ol>
    </section>
    <section
      v-if="citation && activeTab === 'search'"
      ref="citationSection"
      tabindex="-1"
      class="analysis-panel"
      aria-label="引用详情"
    >
      <div class="section-heading">
        <h2>引用原文</h2>
        <button @click="closeCitation">关闭引用</button>
      </div>
      <p>
        {{ citation.filename }} ·
        {{ citation.page ? `第${citation.page}页` : `记录${citation.record}`
        }}{{ citation.record_id ? ` · 编号${citation.record_id}` : "" }}
      </p>
      <p v-if="citation.historical" class="notice">
        这是历史索引引用，保留原版本证据。
      </p>
      <p class="evidence-text">{{ citation.text }}</p>
      <p class="small break-text">
        字符范围 {{ citation.start }}～{{
          citation.end
        }}（起点含、终点不含）；索引 {{ citation.index_id }}
      </p>
      <SourcePreview
        v-if="citationSource"
        :key="citation.chunk_id"
        :project-id="project.id"
        :source="citationSource"
        :initial-page="citationPage"
        :expected-hash="citation.content_hash"
        :highlight="citation"
        back-label="返回检索结果"
        @back="closeCitation"
      />
    </section>
  </template>
</template>
