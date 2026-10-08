import { request } from "./api";

export interface IndexVersion {
  id: string;
  source_id: string;
  status: "queued" | "processing" | "ready" | "failed";
  active: boolean;
  chunk_count: number;
  error: string | null;
  profile: { model: string; revision: string; dimension: number; key: string };
  options: { text_field: string | null; record_id_field: string | null };
  created_at: string;
}
export interface Citation {
  chunk_id: string;
  index_id: string;
  source_id: string;
  filename: string;
  page: number | null;
  record: number | null;
  record_id: string | null;
  text: string;
  start: number;
  end: number;
  historical?: boolean;
  similarity?: number;
  content_hash?: string;
}
export interface OrderStatistics {
  timezone: "Asia/Shanghai";
  paid_order_count: number;
  refund_order_count: number;
  refund_basis: string;
}
export interface MetricResult {
  metric: string;
  filters: {
    start: string;
    end: string;
    region: string | null;
    product: string | null;
  };
  metric_version: string;
  mapping_version: string;
  value: string | null;
  unit: string;
  statistics?: OrderStatistics;
  comparison: {
    start: string;
    end: string;
    value: string | null;
    change_percent: string | null;
    reason: string | null;
    statistics?: OrderStatistics;
    evidence?: MetricResult["evidence"];
  } | null;
  groups: {
    group: string;
    value: string | null;
    contribution_percent: string | null;
    records: number[];
    record_count: number;
  }[];
  warnings: string[];
  evidence: {
    source_id: string;
    filename: string;
    content_hash: string;
    record_count: number;
    records: number[];
    records_truncated: boolean;
    locator: string;
  };
}
export interface MappingResult {
  valid: boolean;
  row_count: number;
  issue_count: number;
  issues: { record: number; message: string }[];
  issues_truncated?: boolean;
}
export interface ToolResult {
  tool: string;
  total?: number;
  truncated?: boolean;
  rows?: { record: number; values: Record<string, string> }[];
  groups?: { group: string; value: string; record_count: number }[];
}
const json = (value: unknown, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
export const analysisApi = {
  model: (p: string) =>
    request<{ profile: IndexVersion["profile"]; downloaded: boolean }>(
      `/projects/${p}/analysis/model`,
    ),
  mapping: (p: string, s: string) =>
    request<{ fields: Record<string, string> }>(
      `/projects/${p}/sources/${s}/mapping`,
    ),
  saveMapping: (p: string, s: string, fields: Record<string, string>) =>
    request<MappingResult>(
      `/projects/${p}/sources/${s}/mapping`,
      json({ fields }, "PUT"),
      60_000,
    ),
  metric: (p: string, s: string, value: unknown) =>
    request<MetricResult>(
      `/projects/${p}/sources/${s}/metrics`,
      json(value),
      60_000,
    ),
  tool: (p: string, s: string, value: unknown) =>
    request<ToolResult>(
      `/projects/${p}/sources/${s}/tools`,
      json(value),
      60_000,
    ),
  indexes: (p: string) => request<IndexVersion[]>(`/projects/${p}/indexes`),
  build: (p: string, s: string, value: unknown) =>
    request<IndexVersion>(`/projects/${p}/sources/${s}/indexes`, json(value)),
  activate: (p: string, id: string) =>
    request<IndexVersion>(`/projects/${p}/indexes/${id}/activate`, json({})),
  search: (p: string, value: unknown) =>
    request<{ results: Citation[]; reason: string | null; notice?: string }>(
      `/projects/${p}/search`,
      json(value),
      120_000,
    ),
  citation: (p: string, id: string) =>
    request<Citation>(`/projects/${p}/citations/${id}`),
};
