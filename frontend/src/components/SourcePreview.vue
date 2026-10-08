<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api, message } from "../api";
import type { DataSource, Preview } from "../types";
const props = defineProps<{
  projectId: string;
  source: DataSource;
  initialPage?: number;
  expectedHash?: string;
  backLabel?: string;
  highlight?: {
    page: number | null;
    record: number | null;
    start: number;
    end: number;
    text: string;
  };
}>();
defineEmits<{ back: [] }>();
const dataScroll = ref<HTMLElement | null>(null);
const pdfText = ref<HTMLElement | null>(null);
const hasOverflow = ref(false);
let resizeObserver: ResizeObserver | undefined;
function measureOverflow() {
  const node = dataScroll.value;
  hasOverflow.value = !!node && node.scrollWidth > node.clientWidth + 1;
}
function observeScroll() {
  // 预览翻页会替换表格节点，先解绑旧观察目标，避免重复监听及陈旧滚动提示。
  resizeObserver?.disconnect();
  if (dataScroll.value) {
    resizeObserver?.observe(dataScroll.value);
    const table = dataScroll.value.querySelector("table");
    if (table) resizeObserver?.observe(table);
  }
  measureOverflow();
}
onMounted(() => {
  resizeObserver = new ResizeObserver(measureOverflow);
  observeScroll();
});
watch(dataScroll, observeScroll, { flush: "post" });
onUnmounted(() => {
  generation++;
  resizeObserver?.disconnect();
});
const preview = ref<Preview | null>(null);
const page = ref(1);
const loading = ref(false);
const error = ref("");
const downloadUrl = computed(
  () =>
    `/api/projects/${props.projectId}/sources/${props.source.id}/download${props.expectedHash ? `?expected_hash=${encodeURIComponent(props.expectedHash)}` : ""}`,
);
let generation = 0;
const pages = computed(() =>
  preview.value?.kind === "csv"
    ? Math.max(1, Math.ceil(preview.value.total_rows / preview.value.page_size))
    : props.source.kind === "csv"
      ? Math.max(1, Math.ceil((props.source.metadata_json.row_count ?? 0) / 20))
      : (preview.value?.page_count ??
        props.source.metadata_json.page_count ??
        1),
);
const highlightedText = computed(() => {
  if (preview.value?.kind !== "pdf") return null;
  const target = props.highlight;
  if (!target || target.page !== page.value) return null;
  // 服务端范围按 Unicode 字符计数；Array.from 避免 emoji 的 UTF-16 长度造成偏移。
  const chars = Array.from(preview.value.text);
  const selected = chars.slice(target.start, target.end).join("");
  if (target.start < 0 || target.end > chars.length || selected !== target.text)
    return null;
  return {
    before: chars.slice(0, target.start).join(""),
    selected,
    after: chars.slice(target.end).join(""),
  };
});
watch(
  [pdfText, highlightedText],
  () => {
    const node = pdfText.value;
    const mark = node?.querySelector("mark");
    // 桌面 PDF 使用内部滚动；引用在页尾时也要直接可见，不改变整个页面的位置。
    if (node && mark && node.scrollHeight > node.clientHeight + 1)
      node.scrollTop +=
        mark.getBoundingClientRect().top -
        node.getBoundingClientRect().top -
        16;
  },
  { flush: "post" },
);
function highlightedRow(index: number) {
  return (
    preview.value?.kind === "csv" &&
    props.highlight?.record ===
      (page.value - 1) * preview.value.page_size + index + 1
  );
}
async function load() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  preview.value = null;
  try {
    const result = await api.preview(
      props.projectId,
      props.source.id,
      page.value,
      props.expectedHash,
    );
    if (current === generation) preview.value = result;
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    if (current === generation) loading.value = false;
  }
}
watch(
  () => [
    props.projectId,
    props.source.id,
    props.initialPage,
    props.expectedHash,
  ],
  () => {
    page.value = Math.min(Math.max(1, props.initialPage ?? 1), pages.value);
    void load();
  },
  { immediate: true },
);
function turn(delta: number) {
  if (
    loading.value ||
    page.value + delta < 1 ||
    page.value + delta > pages.value
  )
    return;
  page.value += delta;
  void load();
}
</script>

<template>
  <section class="preview-panel" aria-label="文件预览">
    <div class="section-heading preview-heading">
      <div>
        <p class="section-label">文件预览</p>
        <h2>{{ source.filename }}</h2>
      </div>
      <div class="preview-actions">
        <button class="mobile-only compact-button" @click="$emit('back')">
          {{ backLabel ?? "返回数据源" }}
        </button>
        <a class="button" :href="downloadUrl">下载原文件</a>
      </div>
    </div>
    <template v-if="source.kind === 'csv'">
      <dl class="quality-summary">
        <div>
          <dt>数据行</dt>
          <dd>{{ source.metadata_json.row_count?.toLocaleString() }}</dd>
        </div>
        <div>
          <dt>字段</dt>
          <dd>{{ source.metadata_json.columns?.length }}</dd>
        </div>
        <div>
          <dt>缺失单元格</dt>
          <dd>{{ source.metadata_json.missing_cells }}</dd>
        </div>
        <div>
          <dt>重复行</dt>
          <dd>{{ source.metadata_json.duplicate_rows }}</dd>
        </div>
      </dl>
      <details class="field-details">
        <summary>字段信息</summary>
        <div class="table-scroll">
          <table class="field-table" aria-label="字段信息">
            <thead>
              <tr>
                <th scope="col">字段名称</th>
                <th scope="col">推断类型</th>
                <th scope="col">缺失值</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="column in source.metadata_json.columns"
                :key="column.name"
              >
                <td>{{ column.name }}</td>
                <td>{{ column.dtype }}</td>
                <td class="numeric">{{ column.missing_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted small">
          {{ source.metadata_json.warnings?.join(" ") }}
        </p>
      </details>
    </template>
    <dl v-else class="quality-summary">
      <div>
        <dt>页数</dt>
        <dd>{{ source.metadata_json.page_count }}</dd>
      </div>
      <div>
        <dt>文本字符</dt>
        <dd>{{ source.metadata_json.text_char_count?.toLocaleString() }}</dd>
      </div>
      <div>
        <dt>无文本页面</dt>
        <dd>{{ source.metadata_json.pages_without_text }}</dd>
      </div>
    </dl>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <button
      v-if="error"
      class="compact-button"
      :disabled="loading"
      @click="load"
    >
      重试预览
    </button>
    <p v-if="loading" class="muted">正在读取预览…</p>
    <template v-if="preview">
      <p v-if="preview.kind === 'csv' && hasOverflow" class="scroll-hint">
        可左右滑动查看其余列
      </p>
      <div
        v-if="preview.kind === 'csv'"
        ref="dataScroll"
        class="table-scroll"
        role="region"
        aria-label="数据预览滚动区域"
        :tabindex="hasOverflow ? 0 : undefined"
      >
        <table class="data-table" aria-label="数据预览">
          <thead>
            <tr>
              <th v-for="column in preview.columns" :key="column" scope="col">
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, index) in preview.rows"
              :key="index"
              :class="{ 'citation-row': highlightedRow(index) }"
              :aria-label="highlightedRow(index) ? '引用对应记录' : undefined"
            >
              <td v-for="column in preview.columns" :key="column">
                <span
                  v-if="row[column] === null || row[column] === ''"
                  class="null-value"
                  >空值</span
                ><template v-else>{{ row[column] }}</template>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!preview.rows.length" class="muted">当前页没有数据。</p>
      </div>
      <p v-if="preview.kind === 'pdf' && preview.text_truncated" class="notice">
        本页预览仅显示前 100,000 个字符，完整内容请下载原文件查看。
      </p>
      <pre
        v-if="preview.kind === 'pdf'"
        ref="pdfText"
        class="pdf-text"
      ><template v-if="highlightedText">{{ highlightedText.before }}<mark class="citation-highlight">{{ highlightedText.selected }}</mark>{{ highlightedText.after }}</template><template v-else>{{ preview.text || "当前页没有可提取文本。" }}</template></pre>
    </template>
    <div class="pagination">
      <span>第 {{ page }} / {{ pages }} 页</span>
      <div class="section-actions">
        <button :disabled="loading || page <= 1" @click="turn(-1)">
          上一页</button
        ><button :disabled="loading || page >= pages" @click="turn(1)">
          下一页
        </button>
      </div>
    </div>
  </section>
</template>
