<script setup lang="ts">
import type { AgentReport, ReportSource } from "../../agent";
import ReportChart from "./ReportChart.vue";
const props = defineProps<{ report: AgentReport; selectedId?: string }>();
const emit = defineEmits<{
  evidence: [source: ReportSource, event: MouseEvent];
}>();
const metricLabels: Record<string, string> = {
  net_sales: "净销售额",
  paid_sales: "实付金额",
  paid_orders: "已支付订单数",
  refund_rate: "退款订单比例",
};
function source(id: string) {
  return props.report.sources.find((s) => s.id === id);
}
function open(id: string, event: MouseEvent) {
  const value = source(id);
  if (value) emit("evidence", value, event);
}
function findingLabel(finding: AgentReport["findings"][number]) {
  if (finding.category === "document_quote") return "原文摘录";
  if (finding.category?.startsWith("calculated_")) return "计算事实";
  return { fact: "事实", inference: "待验证推断", unknown: "无法确认" }[
    finding.kind
  ];
}
</script>
<template>
  <article class="agent-report">
    <h2>{{ report.title }}</h2>
    <p v-if="report.version !== 'agent-report-v2'" class="notice">
      旧版报告按旧规则生成，事实文字仍需结合原文核对。
    </p>
    <p class="report-summary">{{ report.summary }}</p>
    <div v-if="report.metrics.length" class="agent-metrics">
      <section v-for="metric in report.metrics" :key="metric.id">
        <h3>{{ metricLabels[metric.metric] ?? metric.metric }}</h3>
        <strong
          >{{ metric.value ?? "无定义"
          }}<small> {{ metric.unit }}</small></strong
        >
        <p class="small">
          {{ metric.filters.start }} 至 {{ metric.filters.end }}
        </p>
        <p v-if="metric.comparison" class="small">
          基期：{{ metric.comparison.value ?? "无定义" }} · 变化：{{
            metric.comparison.change_percent === null
              ? "无定义"
              : `${metric.comparison.change_percent}%`
          }}
        </p>
        <p class="muted small">
          证据 {{ metric.id }} · {{ metric.evidence.filename }}
        </p>
        <button
          v-if="source(metric.id)"
          class="compact-button"
          @click="open(metric.id, $event)"
        >
          查看指标来源
        </button>
      </section>
    </div>
    <div v-if="report.chart_specs.length" class="report-charts">
      <ReportChart
        v-for="(spec, index) in report.chart_specs"
        :key="`${spec.result_id}-${index}`"
        :spec="spec"
        @evidence="(id, event) => open(id, event)"
      />
    </div>
    <h3>分析结论</h3>
    <ul class="agent-findings">
      <li v-for="(finding, index) in report.findings" :key="index">
        <span class="agent-finding-label">{{ findingLabel(finding) }}</span>
        <p>{{ finding.text }}</p>
        <div
          v-if="finding.evidence_ids.length"
          class="section-actions finding-citations"
        >
          <template
            v-for="(id, referenceIndex) in finding.evidence_ids"
            :key="id"
          >
            <button
              v-if="source(id)"
              class="compact-button"
              :title="`${source(id)?.filename} · ${id}`"
              @click="open(id, $event)"
            >
              查看引用 {{ referenceIndex + 1 }}
            </button>
            <span v-else class="muted small">引用 {{ id }}</span>
          </template>
        </div>
      </li>
    </ul>
    <template v-if="report.recommendations.length"
      ><h3>建议行动</h3>
      <ul>
        <li v-for="(item, i) in report.recommendations" :key="i">{{ item }}</li>
      </ul></template
    >
    <h3>证据来源</h3>
    <p v-if="!report.sources.length" class="muted">没有可用于该结论的证据。</p>
    <ul class="agent-evidence">
      <li
        v-for="value in report.sources"
        :key="value.id"
        :class="{ selected: selectedId === value.id }"
      >
        <div>
          <strong>{{ value.filename }}</strong>
          <p class="muted small">
            {{
              value.kind === "document"
                ? value.page
                  ? `第${value.page}页`
                  : `记录${value.record}`
                : `${value.record_count ?? 0}条数据记录`
            }}
            · {{ value.id }}
          </p>
          <p v-if="value.records_truncated" class="muted small">
            定位记录为引用样本，统计使用全部符合条件的记录。
          </p>
        </div>
        <button @click="emit('evidence', value, $event)">查看原文</button>
      </li>
    </ul>
    <h3>限制与待确认</h3>
    <ul>
      <li v-for="(item, i) in report.limitations" :key="i">{{ item }}</li>
    </ul>
  </article>
</template>
