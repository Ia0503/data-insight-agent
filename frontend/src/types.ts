export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}
export interface Health {
  status: string;
  max_upload_mb: number;
}
export interface Column {
  name: string;
  dtype: string;
  missing_count: number;
}
export interface DataSource {
  id: string;
  project_id: string;
  filename: string;
  kind: "csv" | "pdf";
  size_bytes: number;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
  metadata_json: {
    row_count?: number;
    columns?: Column[];
    missing_cells?: number;
    duplicate_rows?: number;
    warnings?: string[];
    page_count?: number;
    text_char_count?: number;
    pages_without_text?: number;
  };
}
export type Preview =
  | {
      kind: "csv";
      columns: string[];
      // 当前服务端保留 CSV 单元格文本；兼容已打开页面可能收到的旧数值/null 响应。
      rows: Record<string, string | number | null>[];
      total_rows: number;
      page: number;
      page_size: number;
    }
  | {
      kind: "pdf";
      text: string;
      page: number;
      page_count: number;
      text_truncated?: boolean;
    };
