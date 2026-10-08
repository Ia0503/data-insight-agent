<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import type { EChartsType } from "echarts/core";
import type { AgentReport } from "../../agent";
const props = defineProps<{ spec: AgentReport["chart_specs"][number] }>();
defineEmits<{ evidence: [id: string, event: MouseEvent] }>();
const element = ref<HTMLElement | null>(null);
const error = ref("");
let instance: EChartsType | undefined;
let observer: ResizeObserver | undefined;
let generation = 0;
const temporal = computed(() => {
  const labels = props.spec.labels;
  return (
    props.spec.time_axis !== false &&
    labels.length > 0 &&
    labels.every(
      (v) =>
        /^\d{4}-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?$/.test(v) &&
        Number.isFinite(Date.parse(v)) &&
        new Date(v).toISOString().startsWith(v),
    ) &&
    labels.every((v, i) => i === 0 || labels[i - 1]! < v)
  );
});
const kind = computed(() =>
  props.spec.kind === "line" && temporal.value ? "line" : "bar",
);
const values = computed(() =>
  props.spec.values.map((v) => {
    if (typeof v !== "string") return null;
    const number = Number(v);
    return v.trim() &&
      Number.isFinite(number) &&
      Math.abs(number) <= Number.MAX_SAFE_INTEGER
      ? number
      : null;
  }),
);
const unsafe = computed(() =>
  props.spec.values.some((v, i) => v !== null && values.value[i] === null),
);
async function render() {
  const version = ++generation;
  observer?.disconnect();
  instance?.dispose();
  instance = undefined;
  error.value = "";
  if (
    !element.value ||
    !props.spec.labels.length ||
    props.spec.labels.length !== props.spec.values.length
  ) {
    error.value = "图表数据不完整，请查看原始数值。";
    return;
  }
  try {
    const { createChart } = await import("../../chartRuntime");
    if (version !== generation || !element.value) return;
    instance = createChart(element.value);
    instance.setOption({
      animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
      aria: { enabled: true },
      grid: {
        left: 12,
        right: 16,
        top: 24,
        bottom: props.spec.labels.length > 8 ? 72 : 24,
        containLabel: true,
      },
      tooltip: {
        trigger: "axis",
        renderMode: "richText",
        confine: true,
        formatter: (points: { dataIndex: number }[]) => {
          const index = points[0]?.dataIndex ?? 0;
          return `${props.spec.labels[index]}\n${props.spec.values[index] ?? "无定义"} ${props.spec.unit ?? ""}`;
        },
      },
      xAxis: {
        type: "category",
        data: props.spec.labels,
        axisLabel: { color: "#62625d", width: 88, overflow: "truncate" },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "#62625d" },
        splitLine: { lineStyle: { color: "#e2e2db" } },
      },
      dataZoom:
        props.spec.labels.length > 8
          ? [
              {
                type: "slider",
                startValue: 0,
                endValue: 7,
                height: 24,
                bottom: 8,
              },
              { type: "inside" },
            ]
          : [],
      series: [
        {
          type: kind.value,
          data: values.value,
          itemStyle: { color: "#555550" },
          lineStyle: { color: "#555550" },
          connectNulls: false,
          barMaxWidth: 52,
        },
      ],
    });
    observer = new ResizeObserver(() => instance?.resize());
    observer.observe(element.value);
  } catch {
    if (version === generation)
      error.value = "图表加载失败，原始数值仍可查看。";
  }
}
watch(() => [element.value, props.spec], render, {
  flush: "post",
  immediate: true,
});
onUnmounted(() => {
  generation++;
  observer?.disconnect();
  instance?.dispose();
});
</script>
<template>
  <section class="report-chart">
    <h3>{{ spec.title }}</h3>
    <p v-if="spec.kind === 'line' && kind === 'bar'" class="muted small">
      此分组没有可靠的时间顺序，按分类柱状图展示。
    </p>
    <p v-if="unsafe" class="notice">部分数值无法安全绘制，请核对原始数值表。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div
      ref="element"
      class="chart-canvas"
      role="img"
      :aria-label="`${spec.title}，完整数值见下表`"
    />
    <details class="chart-values">
      <summary>查看原始数值表</summary>
      <p v-if="unsafe" class="muted small">
        高精度数值保留原文；表格较宽时，可在表内横向滚动查看完整数值。
      </p>
      <div
        class="table-scroll"
        tabindex="0"
        role="region"
        aria-label="图表原始数值滚动区域"
      >
        <table class="data-table" aria-label="图表原始数值">
          <thead>
            <tr>
              <th>分组</th>
              <th>数值{{ spec.unit ? `（${spec.unit}）` : "" }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(label, i) in spec.labels" :key="i">
              <td>{{ label }}</td>
              <td>{{ spec.values[i] ?? "无定义" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
    <button
      class="compact-button"
      @click="
        $emit('evidence', spec.evidence_ids?.[0] ?? spec.result_id, $event)
      "
    >
      查看统计来源
    </button>
  </section>
</template>
