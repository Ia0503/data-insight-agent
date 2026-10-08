import { expect, test } from "@playwright/test";
import { json, setup } from "./agent.fixture";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { fileURLToPath } from "node:url";

test("draft and uncertain original submission survive reload without auto submit", async ({
  page,
}) => {
  const data = await setup(page);
  const ids: string[] = [];
  await page.route("**/api/projects/*/agent/runs", async (route) => {
    if (route.request().method() === "GET") return route.fulfill(json([]));
    ids.push(route.request().postDataJSON().request_id);
    if (ids.length === 1) return route.abort("failed");
    return route.fulfill(json({ ...data.run, request_id: ids[0] }, 202));
  });
  await data.open();
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill("刷新后仍需保留的问题");
  await page.reload();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
    "刷新后仍需保留的问题",
  );
  expect(ids).toHaveLength(0);
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认原提交" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "确认原提交" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toBeDisabled();
  expect(ids).toHaveLength(1);
  await page.getByRole("button", { name: "确认原提交" }).click();
  await expect(page).toHaveURL(new RegExp(`/agent/runs/${data.run.id}$`));
  expect(ids).toEqual([ids[0], ids[0]]);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "销售分析报告" }),
  ).toBeVisible();
  expect(ids).toHaveLength(2);
});

test("drafts are project scoped and report reuse only prefills a form", async ({
  page,
}) => {
  const first = await setup(page, { history: true });
  const second = await setup(page);
  let posts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST") posts++;
  });
  await page.goto(`/projects/${first.project.id}/agent`);
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill("第一个项目的草稿");
  await second.open();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue("");
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill("第二个项目的草稿");
  await page.goto(`/projects/${first.project.id}/agent`);
  await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
    "第一个项目的草稿",
  );
  await first.open();
  await page.getByRole("button", { name: "用此问题新建分析" }).click();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
    first.run.question,
  );
  expect(posts).toBe(0);
});

test("obsolete source selections are removed from an ordinary restored draft", async ({
  page,
}) => {
  const data = await setup(page);
  await data.open();
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill("保留问题，剔除已删除文件");
  await page.route(`**/api/projects/${data.project.id}/sources`, (route) =>
    route.fulfill(json([])),
  );
  await page.reload();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
    "保留问题，剔除已删除文件",
  );
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      (key) => JSON.parse(sessionStorage.getItem(key)!).sourceIds,
      `newai:analysis-draft:${data.project.id}`,
    ),
  ).toEqual([]);
});

test("history filters and cursor survive navigation and same-query refresh works", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  const queries: URLSearchParams[] = [];
  await page.route("**/api/projects/*/agent/history?*", (route) => {
    const query = new URL(route.request().url()).searchParams;
    queries.push(query);
    return route.fulfill(
      json({
        items: [
          {
            ...data.run,
            report_title: query.has("cursor") ? "第二页报告" : "第一页报告",
            created_at: "2026-10-06T16:05:00Z",
          },
        ],
        total: 21,
        next_cursor: query.has("cursor") ? null : "cursor-next",
      }),
    );
  });
  await page.route("**/api/projects/*/agent/history", (route) =>
    route.fulfill(
      json({
        items: [{ ...data.run, report_title: "第一页报告" }],
        total: 21,
        next_cursor: "cursor-next",
      }),
    ),
  );
  await page.goto(`/projects/${data.project.id}/agent/history`);
  await expect(page.getByRole("button", { name: "查询历史" })).toBeEnabled();
  const initialQueries = queries.length;
  await page.getByRole("textbox", { name: "关键词" }).fill("销售%_");
  await page.getByRole("combobox", { name: "任务状态" }).click();
  await page.getByRole("option", { name: "已完成", exact: true }).click();
  await page.getByLabel("开始日期").fill("2026-10-07");
  await page.getByLabel("结束日期").fill("2026-10-07");
  await page.getByRole("button", { name: "查询历史" }).click();
  await expect(page.locator(".history-list")).toContainText("2026/10/07 00:05");
  await expect.poll(() => queries.length).toBe(initialQueries + 1);
  expect(Object.fromEntries(queries.at(-1)!)).toEqual({
    q: "销售%_",
    status: "succeeded",
    start: "2026-10-07",
    end: "2026-10-07",
  });
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.locator(".history-list")).toContainText("第二页报告");
  expect(queries.at(-1)!.get("cursor")).toBe("cursor-next");
  await page.goBack();
  await expect(page.locator(".history-list")).toContainText("第一页报告");
  const before = queries.length;
  await page.getByRole("button", { name: "查询历史" }).click();
  await expect.poll(() => queries.length).toBe(before + 1);
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page).toHaveURL(`/projects/${data.project.id}/agent/history`);
});

test("native SSE reconnects with event id, deduplicates events and loads terminal report without POST", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  data.run.status = "running";
  let reads = 0,
    connections = 0,
    posts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST") posts++;
  });
  await page.route(`**/api/projects/*/agent/runs/${data.run.id}`, (route) => {
    reads++;
    return route.fulfill(
      json({
        ...data.run,
        status: reads > 1 ? "succeeded" : "running",
        report: reads > 1 ? data.run.report : null,
      }),
    );
  });
  await page.route("**/api/projects/*/agent/runs/*/events?*", (route) =>
    route.fulfill(
      json([
        {
          sequence: 1,
          kind: "queued",
          message: "初始事件",
          created_at: data.project.created_at,
        },
      ]),
    ),
  );
  const event = (seq: number) =>
    `id: ${seq}\nevent: progress\ndata: ${JSON.stringify({ sequence: seq, kind: "tool_end", message: `实时事件${seq}`, created_at: data.project.created_at })}\n\n`;
  const resumes: (string | undefined)[] = [];
  const afters: string[] = [];
  let finish: (() => void) | undefined;
  const server = createServer((request, response) => {
    connections++;
    resumes.push(request.headers["last-event-id"] as string | undefined);
    afters.push(
      new URL(request.url!, "http://localhost").searchParams.get("after")!,
    );
    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      "Access-Control-Allow-Origin": "*",
    });
    if (connections === 1) {
      response.write(`retry: 50\n\n${event(2)}${event(2)}`);
      setTimeout(() => response.end(), 100);
    } else {
      response.write(`${event(3)}event: heartbeat\ndata: {"sequence":3}\n\n`);
      finish = () =>
        response.end('event: complete\ndata: {"status":"succeeded"}\n\n');
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  try {
    // A real chunked HTTP transport verifies native EventSource parsing/reconnect.
    await page.route("**/api/projects/*/agent/runs/*/stream?*", (route) =>
      route.continue({
        url: `http://127.0.0.1:${address.port}${new URL(route.request().url()).pathname}${new URL(route.request().url()).search}`,
      }),
    );
    await data.open();
    await expect(
      page.locator(".run-timeline p").filter({ hasText: "实时事件2" }),
    ).toHaveCount(1);
    await expect(
      page.locator(".run-timeline p").filter({ hasText: "实时事件3" }),
    ).toHaveCount(1);
    finish?.();
    await expect(page.locator(".agent-run-status")).toContainText("已完成");
    await expect(
      page.getByRole("heading", { name: "销售分析报告" }),
    ).toBeVisible();
    expect(connections).toBe(2);
    expect(afters).toEqual(["1", "1"]);
    expect(resumes).toEqual([undefined, "2"]);
    expect(reads).toBe(2);
    expect(posts).toBe(0);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("late manual refresh cannot undo cancellation acknowledgement", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  data.run.status = "running";
  let calls = 0,
    release: (() => void) | undefined;
  await page.route(
    `**/api/projects/*/agent/runs/${data.run.id}`,
    async (route) => {
      calls++;
      const before = { ...data.run, report: null };
      if (calls === 2)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      await route.fulfill(json(before));
    },
  );
  await page.route("**/api/projects/*/agent/runs/*/stream?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: state\ndata: {"status":"running"}\n\n',
    }),
  );
  await page.route("**/cancel", (route) => {
    data.run.status = "cancelled";
    return route.fulfill(
      json({ ...data.run, report: null, cancel_requested: true }),
    );
  });
  await data.open();
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect.poll(() => !!release).toBe(true);
  await page.getByRole("button", { name: "取消任务" }).click();
  await expect(page.locator(".agent-run-status")).toContainText("已取消");
  release?.();
  await expect.poll(() => calls).toBe(3);
  await expect(page.locator(".agent-run-status")).toContainText("已取消");
});

for (const width of [320, 390, 768, 1440]) {
  test(`charts and evidence remain usable at ${width}px with original values and focus return`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const data = await setup(page, { history: true });
    data.run.report.chart_specs = [
      {
        kind: "line",
        result_id: "R1",
        title: "分组净销售额",
        labels: ["2026-08", "2026-09", "2026-10", "2026-11"],
        values: ["0.00", "-25.50", null, "9007199254740993.00"],
        unit: "元",
        time_axis: true,
        evidence_ids: ["R1"],
      },
    ];
    await data.open();
    await expect(page.locator(".chart-canvas svg")).toBeVisible();
    await expect(
      page.getByText("部分数值无法安全绘制", { exact: false }),
    ).toBeVisible();
    await page.getByText("查看原始数值表", { exact: true }).click();
    await expect(
      page.getByRole("table", { name: "图表原始数值" }),
    ).toContainText("9007199254740993.00");
    await expect(
      page.getByRole("table", { name: "图表原始数值" }),
    ).toContainText("-25.50");
    await expect(
      page.getByRole("table", { name: "图表原始数值" }),
    ).toContainText("无定义");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    const trigger = page.getByRole("button", { name: "查看统计来源" });
    await trigger.click();
    await expect(page.locator("tr.citation-row")).toContainText("0001");
    const original = page.getByRole("region", { name: "报告证据原文" });
    await expect(original).toBeFocused();
    if (width <= 768) await expect(page.locator(".agent-report")).toBeHidden();
    else if (width === 1440)
      await expect(page.locator(".agent-report")).toBeVisible();
    await page.screenshot({
      path: fileURLToPath(
        new URL(`../results/step4-evidence-${width}.png`, import.meta.url),
      ),
      fullPage: true,
    });
    await original.press("Escape");
    await expect(trigger).toBeFocused();
    await expect(page.locator(".agent-report")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: fileURLToPath(
        new URL(`../results/step4-chart-${width}.png`, import.meta.url),
      ),
      fullPage: true,
    });
    await page.getByRole("button", { name: "用此问题新建分析" }).click();
    await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
      data.run.question,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.goto(`/projects/${data.project.id}/agent/history`);
    await expect(page.locator(".history-list")).toContainText("销售分析报告");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: fileURLToPath(
        new URL(`../results/step4-history-${width}.png`, import.meta.url),
      ),
      fullPage: true,
    });
  });
}

test("categorical and invalid calendar labels use bars and escape markup", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  data.run.report.chart_specs = [
    {
      kind: "line",
      result_id: "R1",
      title: "分类数据",
      labels: ['<img src=x onerror="window.chartInjected=true">', "2026-02-31"],
      values: ["1.00", "2.00"],
    },
  ];
  await data.open();
  await expect(page.locator(".chart-canvas svg")).toBeVisible();
  await expect(
    page.getByText("此分组没有可靠的时间顺序", { exact: false }),
  ).toBeVisible();
  await page.getByText("查看原始数值表").click();
  await expect(page.getByRole("table", { name: "图表原始数值" })).toContainText(
    "<img src=x",
  );
  expect(await page.locator(".report-chart img").count()).toBe(0);
  expect(
    await page.evaluate(
      () => (window as Window & { chartInjected?: boolean }).chartInjected,
    ),
  ).toBeUndefined();
});

test("long citation ids use compact labels and still locate the correct evidence on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const data = await setup(page, { history: true });
  const id = "R11:f6f767c1-011e-45fc-87aa-7aad054d3e53";
  data.run.report.sources[0]!.id = id;
  data.run.report.findings[0]!.evidence_ids = [id];
  await data.open();
  const button = page.getByRole("button", { name: "查看引用 1", exact: true });
  await expect(button).toHaveAttribute("title", `orders.csv · ${id}`);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await button.click();
  await expect(page.locator("tr.citation-row")).toContainText("0001");
  await page.getByRole("region", { name: "报告证据原文" }).press("Escape");
  await expect(button).toBeFocused();
});

test("demo import shows partial failure and a manual retry reuses the same project", async ({
  page,
}) => {
  const data = await setup(page);
  const files = ["orders.csv", "feedback.csv"].map((filename) => ({
    filename,
    status: "missing",
    error: null,
    index_status: null,
    index_error: null,
  }));
  let attempts = 0;
  const imported: string[] = [];
  let resumeRetry!: () => void;
  const retryGate = new Promise<void>((resolve) => {
    resumeRetry = resolve;
  });
  await page.route("**/api/projects", (route) =>
    route.fulfill(json([data.project])),
  );
  await page.route("**/api/examples/business", (route) =>
    route.request().method() === "POST"
      ? route.fulfill(json(data.project))
      : route.fulfill(
          json({ project: data.project, files, model_downloaded: false }),
        ),
  );
  await page.route("**/api/examples/business/files/*", async (route) => {
    const filename = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").at(-1)!,
    );
    imported.push(filename);
    if (filename === "orders.csv") {
      const attempt = attempts++;
      if (attempt === 0)
        return route.fulfill(json({ detail: "测试写入暂时失败" }, 503));
      if (attempt === 1) await retryGate;
    }
    files.find((f) => f.filename === filename)!.status = "ready";
    return route.fulfill(json({ warning: "模型尚未准备好，可再次补齐索引。" }));
  });
  await page.goto("/");
  await page.getByRole("button", { name: "导入业务示例" }).click();
  await expect(
    page.getByText("测试写入暂时失败", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "查看示例项目" }),
  ).toHaveAttribute("href", `/projects/${data.project.id}`);
  await page.getByRole("button", { name: "导入业务示例" }).click();
  try {
    await expect(
      page.getByRole("button", { name: "正在导入示例…" }),
    ).toBeDisabled();
  } finally {
    resumeRetry();
  }
  await expect
    .poll(() => [...imported])
    .toEqual(["orders.csv", "feedback.csv", "orders.csv", "feedback.csv"]);
  await expect(
    page.getByRole("button", { name: "导入业务示例" }),
  ).toBeEnabled();
  await expect(page.getByText("测试写入暂时失败", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.locator(".example-files")).toContainText("已就绪");
  await expect(
    page.locator(".example-files li").filter({ hasText: "orders.csv" }),
  ).toContainText("已就绪");
  await page.screenshot({
    path: fileURLToPath(
      new URL("../results/step4-example.png", import.meta.url),
    ),
    fullPage: true,
  });
});
