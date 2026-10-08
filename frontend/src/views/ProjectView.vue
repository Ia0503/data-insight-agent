<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, dateLabel, message } from "../api";
import type { DataSource, Project } from "../types";
import SourcePreview from "../components/SourcePreview.vue";
const route = useRoute();
const project = ref<Project | null>(null);
const sources = ref<DataSource[]>([]);
const selected = ref<DataSource | null>(null);
const error = ref("");
const notice = ref("");
const loading = ref(false);
const busy = ref(false);
const editing = ref(false);
const name = ref("");
const description = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const sourcesSection = ref<HTMLElement | null>(null);
const previewTarget = ref<HTMLElement | null>(null);
const maxUploadMb = ref(20);
const labels = { ready: "已就绪", failed: "处理失败", processing: "处理中" };
// 路由切换会复用详情组件；每个异步操作只更新自己所属的项目生命周期。
let generation = 0;
let refreshGeneration = 0;
let poll: ReturnType<typeof setInterval> | undefined;
watch(
  () => route.params.id,
  async (id) => {
    const current = ++generation;
    refreshGeneration++;
    project.value = null;
    sources.value = [];
    selected.value = null;
    error.value = "";
    notice.value = "";
    busy.value = false;
    editing.value = false;
    loading.value = true;
    if (poll) clearInterval(poll);
    try {
      const [value, files, health] = await Promise.all([
        api.project(String(id)),
        api.sources(String(id)),
        api.health(),
      ]);
      if (current !== generation) return;
      project.value = value;
      sources.value = files;
      name.value = value.name;
      description.value = value.description;
      maxUploadMb.value = health.max_upload_mb;
      poll = setInterval(() => {
        if (sources.value.some((source) => source.status === "processing"))
          void refresh();
      }, 2000);
    } catch (e) {
      if (current === generation) error.value = message(e);
    } finally {
      if (current === generation) loading.value = false;
    }
  },
  { immediate: true },
);
onUnmounted(() => {
  generation++;
  if (poll) clearInterval(poll);
});
async function refresh() {
  if (!project.value) return;
  const id = project.value.id;
  const current = generation;
  const request = ++refreshGeneration;
  error.value = "";
  try {
    const value = await api.sources(id);
    if (current === generation && request === refreshGeneration)
      sources.value = value;
  } catch (e) {
    if (current === generation && request === refreshGeneration)
      error.value = message(e);
  }
}
function moveOnMobile(target: HTMLElement | null) {
  if (!target || !window.matchMedia("(max-width: 768px)").matches) return;
  target.focus({ preventScroll: true });
  target.scrollIntoView({
    block: "start",
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
}
async function selectSource(source: DataSource) {
  selected.value = source;
  await nextTick();
  moveOnMobile(previewTarget.value);
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file || !project.value || busy.value) return;
  const id = project.value.id;
  const current = generation;
  error.value = "";
  notice.value = "";
  busy.value = true;
  try {
    if (file.size > maxUploadMb.value * 1024 * 1024)
      throw new Error(`文件不能超过 ${maxUploadMb.value} MB。`);
    const source = await api.upload(id, file);
    if (current !== generation) return;
    await refresh();
    if (current !== generation) return;
    if (source.status === "ready") {
      selected.value = source;
      notice.value = "文件已导入，可查看预览。";
    } else {
      notice.value = "原文件已保存，但处理失败，请查看错误提示。";
    }
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    if (current === generation) {
      busy.value = false;
      if (fileInput.value) fileInput.value.value = "";
    }
  }
}
async function retry(source: DataSource) {
  if (!project.value || busy.value) return;
  const id = project.value.id;
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.retry(id, source.id);
    if (current !== generation) return;
    await refresh();
    if (current !== generation) return;
    if (result.status === "ready") selected.value = result;
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function save() {
  if (!project.value || busy.value) return;
  const current = generation;
  const id = project.value.id;
  busy.value = true;
  error.value = "";
  try {
    const value = await api.update(id, name.value, description.value);
    // 服务端完成旧项目的写入后，也不能覆盖当前路由里的新项目。
    if (current !== generation) return;
    project.value = value;
    editing.value = false;
  } catch (e) {
    if (current === generation) error.value = message(e);
  } finally {
    if (current === generation) busy.value = false;
  }
}
function toggleEditing() {
  if (!project.value || busy.value) return;
  if (!editing.value) {
    name.value = project.value.name;
    description.value = project.value.description;
  }
  editing.value = !editing.value;
}
</script>

<template>
  <nav class="breadcrumb" aria-label="当前位置">
    <RouterLink to="/">项目</RouterLink><span aria-hidden="true">/</span
    ><span>项目详情</span>
  </nav>
  <p v-if="loading" class="muted">正在加载项目…</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <template v-if="project">
    <div class="page-heading project-heading">
      <div>
        <h1>{{ project.name }}</h1>
        <p v-if="project.description" class="project-summary">
          {{ project.description }}
        </p>
      </div>
      <div class="actions">
        <RouterLink
          class="button primary"
          :to="`/projects/${project.id}/analysis`"
          >分析与检索</RouterLink
        >
        <RouterLink class="button" :to="`/projects/${project.id}/agent`"
          >智能分析</RouterLink
        >
        <button :disabled="busy" @click="toggleEditing">编辑项目</button>
      </div>
    </div>
    <section v-if="editing" class="form-panel" aria-label="编辑项目">
      <form @submit.prevent="save">
        <label
          >项目名称<input
            v-model="name"
            :disabled="busy"
            required
            maxlength="120" /></label
        ><label
          >项目描述<textarea
            v-model="description"
            :disabled="busy"
            maxlength="2000"
          ></textarea>
        </label>
        <div class="actions">
          <button type="button" :disabled="busy" @click="editing = false">
            取消</button
          ><button class="primary" :disabled="busy">保存修改</button>
        </div>
      </form>
    </section>
    <section
      ref="sourcesSection"
      tabindex="-1"
      aria-labelledby="sources-heading"
      class="sources-section"
    >
      <div class="section-heading">
        <h2 id="sources-heading">
          数据源 <span class="count">{{ sources.length }}</span>
        </h2>
        <div class="section-actions">
          <button class="compact-button" :disabled="busy" @click="refresh">
            刷新</button
          ><button class="primary" :disabled="busy" @click="fileInput?.click()">
            {{ busy ? "正在处理…" : "上传文件" }}
          </button>
        </div>
      </div>
      <p class="file-hint">
        CSV（UTF-8）或文本型 PDF，单文件最大 {{ maxUploadMb }} MB。
      </p>
      <input
        ref="fileInput"
        type="file"
        accept=".csv,.pdf"
        class="visually-hidden"
        aria-label="选择上传文件"
        :disabled="busy"
        @change="upload"
      />
      <p v-if="notice" role="status" class="notice">{{ notice }}</p>
      <div v-if="!sources.length" class="empty-state">
        <h3>尚未添加数据源</h3>
        <p>上传文件后，可查看字段、数据质量和文档内容。</p>
      </div>
      <div v-else class="list-scroll">
        <table class="source-table" aria-label="数据源列表">
          <thead>
            <tr>
              <th scope="col">文件名称</th>
              <th scope="col">类型</th>
              <th scope="col">数据规模</th>
              <th scope="col">大小</th>
              <th scope="col">状态</th>
              <th scope="col" class="align-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="source in sources"
              :key="source.id"
              :class="{ selected: selected?.id === source.id }"
            >
              <td class="source-info">
                <strong>{{ source.filename }}</strong>
                <p class="file-date">{{ dateLabel(source.created_at) }} 导入</p>
                <p v-if="source.error" class="source-error">
                  {{ source.error }}
                </p>
              </td>
              <td class="file-type" data-label="类型">
                {{ source.kind.toUpperCase() }}
              </td>
              <td class="numeric source-scale" data-label="规模">
                {{
                  source.kind === "csv" &&
                  source.metadata_json.row_count !== undefined
                    ? `${source.metadata_json.row_count.toLocaleString()} 行`
                    : source.metadata_json.page_count !== undefined
                      ? `${source.metadata_json.page_count} 页`
                      : "—"
                }}
              </td>
              <td class="numeric source-size" data-label="大小">
                {{ (source.size_bytes / 1024).toFixed(1) }} KB
              </td>
              <td class="source-state">
                <span class="status" :class="source.status">{{
                  labels[source.status]
                }}</span>
              </td>
              <td class="align-right source-actions">
                <button
                  v-if="source.status === 'ready'"
                  class="compact-button"
                  :aria-pressed="selected?.id === source.id"
                  :disabled="busy"
                  @click="selectSource(source)"
                >
                  查看预览</button
                ><button
                  v-else-if="source.status === 'failed'"
                  class="compact-button"
                  :disabled="busy"
                  @click="retry(source)"
                >
                  重试</button
                ><span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <div
      v-if="selected"
      ref="previewTarget"
      tabindex="-1"
      class="preview-target"
      role="region"
      aria-label="当前文件预览"
    >
      <SourcePreview
        :key="selected.id"
        :project-id="project.id"
        :source="selected"
        @back="moveOnMobile(sourcesSection)"
      />
    </div>
  </template>
</template>
