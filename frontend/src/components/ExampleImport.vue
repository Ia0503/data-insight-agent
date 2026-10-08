<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { message, request } from "../api";
import type { Project } from "../types";
const emit = defineEmits<{ imported: [] }>();
interface FileState {
  filename: string;
  status: string;
  error: string | null;
  index_status: string | null;
  index_error: string | null;
}
interface Example {
  project: Project | null;
  files: FileState[];
  model_downloaded: boolean;
}
const opened = ref(false),
  busy = ref(false),
  example = ref<Example | null>(null);
const current = ref(""),
  error = ref(""),
  messages = ref<Record<string, string>>({});
let alive = true;
let timer: ReturnType<typeof setTimeout> | undefined;
onUnmounted(() => {
  alive = false;
  if (timer) clearTimeout(timer);
});
async function refresh() {
  if (timer) clearTimeout(timer);
  timer = undefined;
  try {
    const value = await request<Example>("/examples/business");
    if (!alive) return;
    example.value = value;
    if (
      !busy.value &&
      opened.value &&
      value.files.some((v) =>
        ["queued", "processing"].includes(v.index_status ?? ""),
      )
    )
      timer = setTimeout(refresh, 1500);
  } catch (failure) {
    if (alive) error.value = message(failure);
    return false;
  }
  return true;
}
async function run() {
  if (busy.value) return;
  busy.value = true;
  opened.value = true;
  error.value = "";
  messages.value = {};
  if (timer) clearTimeout(timer);
  try {
    await request<Project>("/examples/business", { method: "POST" });
    if (!(await refresh()) || !alive || !example.value) return;
    for (const asset of example.value.files) {
      if (!alive) return;
      current.value = asset.filename;
      try {
        const result = await request<{ warning: string | null }>(
          `/examples/business/files/${encodeURIComponent(asset.filename)}`,
          { method: "POST" },
          120000,
        );
        if (alive && result.warning)
          messages.value[asset.filename] = result.warning;
      } catch (failure) {
        if (alive) messages.value[asset.filename] = message(failure);
      }
    }
    current.value = "";
    await refresh();
    if (alive) emit("imported");
  } catch (failure) {
    if (alive) error.value = message(failure);
  } finally {
    if (alive) {
      busy.value = false;
      current.value = "";
      if (
        opened.value &&
        example.value?.files.some((v) =>
          ["queued", "processing"].includes(v.index_status ?? ""),
        )
      )
        timer = setTimeout(refresh, 1500);
    }
  }
}
function close() {
  opened.value = false;
  if (timer) clearTimeout(timer);
  timer = undefined;
}
const labels: Record<string, string> = {
  missing: "待导入",
  processing: "处理中",
  ready: "已就绪",
  failed: "失败",
  queued: "排队中",
};
</script>
<template>
  <section class="example-import" aria-label="业务示例导入">
    <button :disabled="busy" @click="run">
      {{ busy ? "正在导入示例…" : "导入业务示例" }}
    </button>
    <div v-if="opened" class="agent-panel example-progress">
      <h2>业务分析示例(test)</h2>
      <p class="muted">
        订单、销售汇总、反馈与中文 PDF
        均为虚构数据，仅用于功能检查。此导入不会调用付费生成模型。
      </p>
      <p v-if="current" role="status">正在处理 {{ current }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <ul v-if="example" class="example-files">
        <li v-for="asset in example.files" :key="asset.filename">
          <strong>{{ asset.filename }}</strong
          ><span
            >{{ labels[asset.status] ?? asset.status
            }}<template v-if="asset.index_status">
              · 索引{{
                labels[asset.index_status] ?? asset.index_status
              }}</template
            ></span
          >
          <p
            v-if="asset.error || asset.index_error || messages[asset.filename]"
            class="error"
          >
            {{ messages[asset.filename] || asset.error || asset.index_error }}
          </p>
        </li>
      </ul>
      <div class="section-actions">
        <RouterLink
          v-if="example?.project"
          class="button"
          :to="`/projects/${example.project.id}`"
          >查看示例项目</RouterLink
        ><button v-if="!busy" @click="close">收起</button>
      </div>
      <p class="muted small">
        再次导入会补齐缺失项；已有文件和映射保留。文件就绪与索引就绪分别显示。
      </p>
    </div>
  </section>
</template>
