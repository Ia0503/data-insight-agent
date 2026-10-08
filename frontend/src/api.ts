import type { DataSource, Health, Preview, Project } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    text: string,
  ) {
    super(text);
  }
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 15_000,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  init?.signal?.addEventListener("abort", abort, { once: true });
  if (init?.signal?.aborted) abort();
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetch(`/api${path}`, {
      ...init,
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body?.detail;
      throw new ApiError(
        response.status,
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? "输入不符合要求，请检查字段长度和内容。"
            : `请求失败（${response.status}），请稍后重试。`,
      );
    }
    if (body === null)
      throw new Error("服务返回了无效响应，请刷新重试或检查后端。");
    return body as T;
  } catch (error) {
    // 浏览器超时不代表服务端撤销写入；提示先刷新确认，避免直接重试造成重复记录。
    if (timedOut)
      throw new Error(
        init?.method && init.method !== "GET"
          ? "请求超时，操作可能已在服务端完成，请刷新确认后再重试。"
          : "请求超时，请检查服务后重试。",
      );
    if (error instanceof TypeError)
      throw new Error("无法连接服务，请检查网络或后端。");
    throw error;
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
}
const json = (value: unknown) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
export const api = {
  health: () => request<Health>("/health"),
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  create: (name: string, description: string) =>
    request<Project>("/projects", {
      method: "POST",
      ...json({ name, description }),
    }),
  update: (id: string, name: string, description: string) =>
    request<Project>(`/projects/${id}`, {
      method: "PUT",
      ...json({ name, description }),
    }),
  sources: (id: string) => request<DataSource[]>(`/projects/${id}/sources`),
  upload: (id: string, file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<DataSource>(
      `/projects/${id}/sources`,
      {
        method: "POST",
        body: data,
      },
      120_000,
    );
  },
  retry: (id: string, source: string) =>
    request<DataSource>(`/projects/${id}/sources/${source}/retry`, {
      method: "POST",
    }),
  preview: (id: string, source: string, page: number, expectedHash?: string) =>
    request<Preview>(
      `/projects/${id}/sources/${source}/preview?page=${page}&page_size=20${expectedHash ? `&expected_hash=${encodeURIComponent(expectedHash)}` : ""}`,
    ),
};
export const message = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
export const dateLabel = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
