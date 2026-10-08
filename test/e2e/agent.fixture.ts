import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import type { AgentReport } from "../../frontend/src/agent";

export const json = (value: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(value),
});

export async function setup(
  page: Page,
  options: { enabled?: boolean; history?: boolean } = {},
) {
  const project = {
    id: randomUUID(),
    name: "智能分析回归(test)",
    description: "",
    created_at: "2026-10-06T00:00:00Z",
    updated_at: "2026-10-06T00:00:00Z",
  };
  const source = {
    id: randomUUID(),
    project_id: project.id,
    filename: "orders.csv",
    kind: "csv",
    status: "ready",
    size_bytes: 100,
    metadata_json: { row_count: 1, columns: [{ name: "order_id" }] },
    error: null,
  };
  const run = {
    id: randomUUID(),
    project_id: project.id,
    request_id: randomUUID(),
    question: "比较九月与八月净销售额",
    provider: "custom",
    model: "模拟展示模型",
    status: "succeeded",
    cancel_requested: false,
    error: null,
    created_at: project.created_at,
    snapshot: {
      sources: [
        { id: source.id, filename: source.filename, content_hash: "test-hash" },
      ],
    },
    plan: ["计算净销售额并核对证据。"],
    tool_count: 1,
    usage: {},
    report: {
      title: "销售分析报告",
      summary: "净销售额较基期下降。",
      findings: [
        { kind: "fact", text: "净销售额低于基期。", evidence_ids: ["R1"] },
        { kind: "unknown", text: "原因尚待核对。", evidence_ids: [] },
      ],
      recommendations: ["核对客户反馈。"],
      limitations: ["统计不能直接证明因果关系。"],
      chart_specs: [] as AgentReport["chart_specs"],
      sources: [
        {
          id: "R1",
          kind: "csv",
          source_id: source.id,
          filename: source.filename,
          content_hash: "test-hash",
          records: [1],
          record_count: 1,
        },
      ],
      metrics: [
        {
          id: "R1",
          metric: "net_sales",
          value: "76000.00",
          unit: "元",
          filters: { start: "2026-09-01", end: "2026-09-30" },
          comparison: { value: "100000.00", change_percent: "-24.00" },
          evidence: { filename: source.filename },
          groups: [],
        },
      ],
    },
  };
  let history = options.history ? [run] : [];
  await page.route(`**/api/projects/${project.id}`, (route) =>
    route.fulfill(json(project)),
  );
  await page.route(`**/api/projects/${project.id}/sources`, (route) =>
    route.fulfill(json([source])),
  );
  await page.route(`**/api/projects/${project.id}/indexes`, (route) =>
    route.fulfill(json([])),
  );
  await page.route("**/api/projects/*/agent/configuration", (route) =>
    route.fulfill(
      json({
        configured: true,
        enabled: options.enabled ?? true,
        provider: "custom",
        model: "模拟展示模型",
        notice: "模拟模型界面验证，不调用真实服务。",
      }),
    ),
  );
  await page.route("**/api/projects/*/agent/runs", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      run.request_id = body.request_id;
      run.question = body.question;
      history = [run];
      await route.fulfill(json(run, 202));
    } else await route.fulfill(json(history));
  });
  await page.route(`**/api/projects/*/agent/runs/${run.id}`, (route) =>
    route.fulfill(json(run)),
  );
  await page.route(`**/api/projects/${project.id}/agent/history*`, (route) =>
    route.fulfill(
      json({
        items: history.map((v) => ({ ...v, report_title: v.report.title })),
        total: history.length,
        next_cursor: null,
      }),
    ),
  );
  await page.route("**/api/projects/*/agent/runs/*/events?*", (route) =>
    route.fulfill(
      json([
        {
          sequence: 1,
          kind: "succeeded",
          message: "报告已完成。",
          created_at: project.created_at,
        },
      ]),
    ),
  );
  await page.route("**/preview?*", (route) =>
    route.fulfill(
      json({
        kind: "csv",
        page: 1,
        page_size: 20,
        total_rows: 1,
        columns: ["order_id"],
        rows: [{ order_id: "0001" }],
      }),
    ),
  );
  await page.route("**/api/projects/*/agent/runs/*/stream?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: unavailable\ndata: {"message":"测试轮询降级"}\n\n',
    }),
  );
  return {
    project,
    source,
    run,
    open: () =>
      page.goto(
        options.history
          ? `/projects/${project.id}/agent/runs/${run.id}`
          : `/projects/${project.id}/agent`,
      ),
  };
}
