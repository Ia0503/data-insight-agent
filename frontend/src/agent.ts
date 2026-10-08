import { request } from "./api";
import type { MetricResult } from "./analysis";

export interface ModelStatus {
  configured: boolean;
  enabled: boolean;
  provider: string;
  model: string;
  notice: string;
  password_required?: boolean;
  access_configured?: boolean;
  quota?: {
    limit: number;
    used: number;
    reserved: number;
    remaining: number;
    can_start: boolean;
    unknown_requests: number;
    reset_at: string;
    timezone: "Asia/Shanghai";
  };
}
export interface ReportSource {
  id: string;
  kind: "csv" | "document";
  source_id: string;
  filename: string;
  content_hash: string;
  records?: number[];
  record_count?: number;
  records_truncated?: boolean;
  chunk_id?: string;
  page?: number | null;
  record?: number | null;
}
export interface AgentReport {
  version?: "agent-report-v2";
  title: string;
  summary: string;
  findings: {
    kind: "fact" | "inference" | "unknown";
    text: string;
    evidence_ids: string[];
    fact_id?: string;
    category?: string;
  }[];
  recommendations: string[];
  limitations: string[];
  metrics: (MetricResult & { id: string })[];
  sources: ReportSource[];
  chart_specs: {
    kind: "bar" | "line";
    title: string;
    result_id: string;
    labels: string[];
    values: (string | null)[];
    unit?: string;
    time_axis?: boolean;
    evidence_ids?: string[];
  }[];
}
export type RunStatus =
  "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export interface RunSummary {
  id: string;
  project_id: string;
  request_id: string;
  question: string;
  provider: string;
  model: string;
  status: RunStatus;
  cancel_requested: boolean;
  error: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  report_title?: string | null;
}
export interface AgentRun extends RunSummary {
  snapshot: {
    sources: { id: string; filename: string; content_hash: string }[];
  };
  plan: string[];
  report: AgentReport | null;
  usage: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    model_calls?: number;
    complete?: boolean;
  };
  tool_count: number;
}
export interface AgentEvent {
  sequence: number;
  kind: string;
  message: string;
  created_at: string;
}
const json = (data: unknown) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});
export const agentApi = {
  configuration: (p: string) =>
    request<ModelStatus>(`/projects/${p}/agent/configuration`),
  list: (p: string) => request<RunSummary[]>(`/projects/${p}/agent/runs`),
  run: (p: string, id: string) =>
    request<AgentRun>(`/projects/${p}/agent/runs/${id}`),
  events: (p: string, id: string, after: number) =>
    request<AgentEvent[]>(
      `/projects/${p}/agent/runs/${id}/events?after=${after}`,
    ),
  history: (p: string, params: URLSearchParams) =>
    request<{ items: RunSummary[]; total: number; next_cursor: string | null }>(
      `/projects/${p}/agent/history?${params}`,
    ),
  create: (
    p: string,
    question: string,
    sourceIds: string[],
    requestId: string,
    password?: string,
  ) =>
    request<AgentRun>(
      `/projects/${p}/agent/runs`,
      {
        ...json({ question, source_ids: sourceIds, request_id: requestId }),
        headers: {
          "Content-Type": "application/json",
          ...(password
            ? { "X-Analysis-Password": encodeURIComponent(password) }
            : {}),
        },
      },
      60000,
    ),
  cancel: (p: string, id: string) =>
    request<AgentRun>(`/projects/${p}/agent/runs/${id}/cancel`, json({})),
};
export const activeRun = (status: RunStatus) =>
  status === "queued" || status === "running";
export const runLabels: Record<RunStatus, string> = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
