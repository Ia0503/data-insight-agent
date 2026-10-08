import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const json = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});
async function select(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

// 只模拟展示状态和异步边界；真实模型/数据库由现有 analysis 用例覆盖。
async function workspace(page: Page) {
  const project = {
    id: randomUUID(),
    name: "审查回归(test)",
    description: "",
    created_at: "2026-10-06T00:00:00Z",
    updated_at: "2026-10-06T00:00:00Z",
  };
  const source = {
    id: randomUUID(),
    project_id: project.id,
    filename: "原文.pdf",
    kind: "pdf",
    status: "ready",
    metadata_json: { page_count: 1 },
    size_bytes: 100,
    error: null,
  };
  const profile = {
    model: "模拟展示模型",
    revision: "1",
    dimension: 2,
    key: "test",
  };
  const old = {
    id: randomUUID(),
    source_id: source.id,
    status: "ready",
    active: true,
    chunk_count: 1,
    profile,
    options: {},
    error: null,
    created_at: project.created_at,
  };
  const next = { ...old, id: randomUUID(), active: false };
  const hit = {
    chunk_id: randomUUID(),
    index_id: old.id,
    source_id: source.id,
    filename: source.filename,
    page: 1,
    record: null,
    record_id: null,
    text: "旧版本的证据",
    start: 0,
    end: 6,
    similarity: 0.8,
  };
  let versions = [old, next];
  await page.route(`**/api/projects/${project.id}`, (route) =>
    route.fulfill(json(project)),
  );
  await page.route("**/sources", (route) => route.fulfill(json([source])));
  await page.route("**/analysis/model", (route) =>
    route.fulfill(json({ profile, downloaded: true })),
  );
  await page.route("**/indexes", (route) => route.fulfill(json(versions)));
  await page.route("**/search", (route) =>
    route.fulfill(json({ results: [hit] })),
  );
  await page.route("**/citations/*", (route) => route.fulfill(json(hit)));
  await page.route("**/preview?*", (route) =>
    route.fulfill(
      json({ kind: "pdf", page: 1, page_count: 1, text: hit.text }),
    ),
  );
  return {
    project,
    source,
    old,
    next,
    hit,
    setVersions: (value: typeof versions) => {
      versions = value;
    },
  };
}

test("CSV switching clears stale tool fields and submits only current fields", async ({
  page,
}) => {
  const data = await workspace(page);
  const csv = (filename: string, columns: string[]) => ({
    ...data.source,
    id: randomUUID(),
    kind: "csv",
    filename,
    metadata_json: {
      row_count: 1,
      columns: columns.map((name) => ({
        name,
        dtype: "text",
        missing_count: 0,
      })),
    },
  });
  const orders = csv("orders.csv", ["order_id", "region", "paid_amount"]),
    feedback = csv("feedback.csv", ["feedback_id", "text"]);
  await page.route("**/sources", (route) =>
    route.fulfill(json([orders, feedback])),
  );
  await page.route("**/mapping", (route) =>
    route.fulfill(json({ fields: {} })),
  );
  await page.goto(`/projects/${data.project.id}/analysis`);
  await page.getByText("通用筛选与分组工具", { exact: true }).click();
  await select(page, "等值筛选列", "order_id");
  await page.getByLabel("筛选值", { exact: true }).fill("0001");
  await select(page, "工具", "分组聚合");
  await select(page, "分组列", "region");
  await select(page, "聚合", "求和");
  await select(page, "数值列", "paid_amount");
  await select(page, "订单数据源", "feedback.csv");
  await expect(
    page.getByRole("combobox", { name: "等值筛选列" }),
  ).toContainText("不筛选");
  await expect(page.getByLabel("筛选值", { exact: true })).toHaveValue("");
  await expect(
    page.getByRole("combobox", { name: "分组列", exact: true }),
  ).toContainText("请选择");
  await expect(
    page.getByRole("combobox", { name: "数值列", exact: true }),
  ).toContainText("请选择");
  await select(page, "工具", "筛选记录");
  await page.route("**/tools", (route) =>
    route.fulfill(json({ tool: "filter_data", total: 0, rows: [] })),
  );
  const sent = page.waitForRequest("**/tools");
  await page.getByRole("button", { name: "运行工具" }).click();
  const request = await sent;
  expect(request.url()).toContain(feedback.id);
  expect(request.postDataJSON()).toMatchObject({
    filters: [],
    group: null,
    value: null,
  });
});

for (const automatic of [false, true]) {
  test(`${automatic ? "automatic" : "manual"} index activation clears results and open citation`, async ({
    page,
  }) => {
    const data = await workspace(page);
    if (automatic)
      data.setVersions([data.old, { ...data.next, status: "processing" }]);
    await page.route("**/activate", (route) => {
      data.setVersions([
        { ...data.old, active: false },
        { ...data.next, active: true },
      ]);
      return route.fulfill(json({ ...data.next, active: true }));
    });
    await page.goto(`/projects/${data.project.id}/analysis?tab=search`);
    await page.getByLabel("检索问题").fill("测试版本切换");
    await page.getByRole("button", { name: "检索证据" }).click();
    await page.getByRole("button", { name: "查看引用原文" }).click();
    await expect(page.getByRole("region", { name: "引用详情" })).toBeVisible();
    if (automatic) {
      await page.waitForResponse((response) =>
        response.url().endsWith("/indexes"),
      );
      await expect(
        page.getByRole("region", { name: "引用详情" }),
      ).toBeVisible();
      data.setVersions([
        { ...data.old, active: false },
        { ...data.next, active: true },
      ]);
      await expect(page.getByRole("region", { name: "引用详情" })).toHaveCount(
        0,
      );
    } else {
      await page.getByRole("tab", { name: "文档索引" }).click();
      await page.getByRole("button", { name: "启用此版本" }).click();
      await page.getByRole("tab", { name: "证据检索" }).click();
    }
    await expect(page.locator(".search-results li")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "引用详情" })).toHaveCount(0);
    await expect(page.locator(".notice")).toContainText("重新检索");
  });
}

test("late index polling cannot undo an acknowledged manual activation", async ({
  page,
}) => {
  const data = await workspace(page);
  await page.route("**/activate", (route) =>
    route.fulfill(json({ ...data.next, active: true })),
  );
  await page.goto(`/projects/${data.project.id}/analysis?tab=index`);
  let release!: () => void, entered!: () => void;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route("**/indexes", async (route) => {
    entered();
    await blocked;
    await route.fulfill(json([data.old, data.next]));
  });
  await page.getByRole("button", { name: "刷新索引" }).click();
  await started;
  await page.getByRole("button", { name: "启用此版本" }).click();
  await expect(page.locator(".notice")).toContainText("索引版本已切换");
  const acknowledged = page
    .locator(".index-list li")
    .filter({ hasText: data.next.id });
  await expect(acknowledged).toContainText("当前启用");
  const response = page.waitForResponse((value) =>
    value.url().endsWith("/indexes"),
  );
  release();
  await response;
  await expect(
    page.locator(".index-list li").filter({ hasText: data.old.id }),
  ).toContainText("未启用");
  await expect(acknowledged).toContainText("当前启用");
});

for (const operation of ["search", "citation"]) {
  test(`late ${operation} response cannot restore results after automatic activation`, async ({
    page,
  }) => {
    const data = await workspace(page);
    data.setVersions([data.old, { ...data.next, status: "processing" }]);
    await page.goto(`/projects/${data.project.id}/analysis?tab=search`);
    await page.getByLabel("检索问题").fill("延迟结果");
    if (operation === "citation") {
      await page.getByRole("button", { name: "检索证据" }).click();
      await expect(
        page.getByRole("button", { name: "查看引用原文" }),
      ).toBeVisible();
    }
    let release!: () => void, entered!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const started = new Promise<void>((resolve) => {
      entered = resolve;
    });
    await page.route(
      operation === "search" ? "**/search" : "**/citations/*",
      async (route) => {
        entered();
        await blocked;
        await route.fulfill(
          json(operation === "search" ? { results: [data.hit] } : data.hit),
        );
      },
    );
    await page
      .getByRole("button", {
        name: operation === "search" ? "检索证据" : "查看引用原文",
      })
      .click();
    await started;
    data.setVersions([
      { ...data.old, active: false },
      { ...data.next, active: true },
    ]);
    await expect(page.locator(".notice")).toContainText("重新检索");
    release();
    await expect(page.getByRole("button", { name: "检索证据" })).toBeEnabled();
    await expect(page.locator(".search-results li")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "引用详情" })).toHaveCount(0);
  });
}
