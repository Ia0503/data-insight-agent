import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { json, setup } from "./agent.fixture";

test("disabled model gate and project entry work without real LLM", async ({
  page,
  request,
}) => {
  const response = await request.post("http://127.0.0.1:8001/api/projects", {
    data: { name: "模型调用关闭回归(test)" },
  });
  const project = await response.json();
  await page.goto(`/projects/${project.id}`);
  await page.getByRole("link", { name: "智能分析", exact: true }).click();
  await expect(
    page.getByText("真实模型调用已关闭", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
});

test("submit displays server metrics, facts, citations and raw CSV highlight", async ({
  page,
}) => {
  const data = await setup(page);
  await data.open();
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill("比较九月与八月净销售额");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "销售分析报告" }),
  ).toBeVisible();
  await expect(page.getByText("76000.00", { exact: false })).toBeVisible();
  await expect(page.getByText("事实", { exact: true })).toBeVisible();
  await expect(page.getByText("无法确认", { exact: true })).toBeVisible();
  const evidence = page.locator(".agent-evidence li");
  await evidence.getByRole("button", { name: "查看原文" }).click();
  await expect(evidence).toHaveClass(/selected/);
  await expect(
    page.getByRole("region", { name: "报告证据原文" }),
  ).toBeVisible();
  await expect(page.locator("tr.citation-row")).toContainText("0001");
  await page.getByRole("button", { name: "返回分析报告" }).click();
  await expect(
    evidence.getByRole("button", { name: "查看原文" }),
  ).toBeFocused();
  await page.screenshot({
    path: fileURLToPath(
      new URL("../results/agent-desktop.png", import.meta.url),
    ),
    fullPage: true,
  });
});

test("historical CSV preview carries its hash and rejects a changed original", async ({
  page,
}) => {
  const fixture = await setup(page, { history: true });
  const digest = "a".repeat(64);
  fixture.run.report.sources[0]!.content_hash = digest;
  await page.route("**/preview?*", (route) => {
    expect(
      new URL(route.request().url()).searchParams.get("expected_hash"),
    ).toBe(digest);
    return route.fulfill(
      json(
        { detail: "原文件已变化，无法按历史证据定位；请重新分析或重新检索。" },
        409,
      ),
    );
  });
  await fixture.open();
  await page.getByRole("button", { name: "查看原文", exact: true }).click();
  const preview = page.getByRole("region", { name: "报告证据原文" });
  await expect(preview.getByRole("alert")).toContainText("原文件已变化");
  await expect(preview.getByRole("row", { name: "引用对应记录" })).toHaveCount(
    0,
  );
  await expect(
    preview.getByRole("link", { name: "下载原文件" }),
  ).toHaveAttribute(
    "href",
    `/api/projects/${fixture.project.id}/sources/${fixture.source.id}/download?expected_hash=${digest}`,
  );
  await preview
    .getByRole("button", { name: "返回分析报告", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "查看原文", exact: true }),
  ).toBeFocused();
});

test("uncertain submission reuses original request id and stops duplicate creation", async ({
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
  await page.getByRole("textbox", { name: "分析问题" }).fill("核对净销售额");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认原提交" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "分析问题" })).toBeDisabled();
  await page.getByRole("button", { name: "确认原提交" }).click();
  await expect(
    page.getByRole("heading", { name: "销售分析报告" }),
  ).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
});

test("new report distinguishes calculated facts from unverified quoted text", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  const quote =
    '资料原文摘录（内容未经事实核实）：“<img src=x onerror="window.reportQuoteExecuted=true">错误业务声明。”';
  Object.assign(data.run.report, {
    version: "agent-report-v2",
    findings: [
      {
        ...data.run.report.findings[0],
        fact_id: "R1:metric",
        category: "calculated_metric",
      },
      {
        kind: "fact",
        text: quote,
        evidence_ids: ["R9:test-chunk"],
        fact_id: "R9:test-chunk:quote",
        category: "document_quote",
      },
      data.run.report.findings[1],
    ],
  });
  await data.open();
  await expect(
    page.locator(".agent-finding-label").filter({ hasText: "原文摘录" }),
  ).toHaveCount(1);
  await expect(
    page.locator(".agent-finding-label").filter({ hasText: "事实" }),
  ).toHaveCount(1);
  await expect(page.locator(".agent-findings")).toContainText(quote);
  await expect(page.locator(".agent-findings img")).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        (window as Window & { reportQuoteExecuted?: boolean })
          .reportQuoteExecuted,
    ),
  ).toBeUndefined();
});

test("cancel acknowledgement cannot be overwritten by late running poll", async ({
  page,
}) => {
  const data = await setup(page, { history: true });
  data.run.status = "running";
  data.run.report = null as unknown as typeof data.run.report;
  let requests = 0,
    release: (() => void) | undefined;
  await page.route(
    `**/api/projects/*/agent/runs/${data.run.id}`,
    async (route) => {
      requests++;
      const before = { ...data.run };
      if (requests === 2)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      await route.fulfill(json(before));
    },
  );
  await page.route("**/cancel", async (route) => {
    data.run.cancel_requested = true;
    data.run.status = "cancelled";
    await route.fulfill(json(data.run));
  });
  await data.open();
  await expect(page.getByRole("button", { name: "取消任务" })).toBeVisible();
  await expect.poll(() => !!release).toBe(true);
  await page.getByRole("button", { name: "取消任务" }).click();
  await expect(page.locator(".agent-run-status")).toContainText("已取消");
  release?.();
  await expect.poll(() => requests).toBe(3);
  await expect(page.locator(".agent-run-status")).toContainText("已取消");
  await expect(page.getByRole("button", { name: "取消任务" })).toHaveCount(0);
});

test("report remains usable at 320px without horizontal overflow and escapes model text", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const data = await setup(page, { history: true });
  data.run.report.summary =
    "<script>window.unwanted=true</script>仅用于转义测试。";
  data.source.filename =
    "这是用于检验窄屏布局和长文件名换行的业务数据源文件.csv";
  await data.open();
  await expect(
    page.getByRole("heading", { name: "销售分析报告" }),
  ).toBeVisible();

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect(await page.locator(".agent-report script").count()).toBe(0);
  const heading = await page
    .getByRole("heading", { name: data.project.name, exact: true })
    .boundingBox();
  const navigation = await page
    .getByRole("link", { name: "项目详情", exact: true })
    .last()
    .boundingBox();
  // 标题按内容取宽度；检查单行高度和导航另起一行，避免将正常自适应宽度判为挤压。
  expect(heading?.height).toBeLessThanOrEqual(40);
  expect(navigation?.y).toBeGreaterThan(
    (heading?.y ?? 0) + (heading?.height ?? 0),
  );
  for (const button of await page
    .locator(".agent-panel button:visible")
    .all()) {
    expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  }
  await page.screenshot({
    path: fileURLToPath(new URL("../results/agent-320.png", import.meta.url)),
    fullPage: true,
  });
});
