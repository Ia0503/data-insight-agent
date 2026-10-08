import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

test("citation highlights respect Unicode, pages, CSV record offsets and escaped text", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("http://127.0.0.1:8001/api/projects", {
      data: { name: "引用边界浏览器验证(test)" },
    })
  ).json();
  const pdfId = randomUUID(),
    csvId = randomUUID();
  const quoted = '<img src=x onerror="window.badMarkup=true">中文引用';
  const before = "😀\n" + "前置说明\n".repeat(80);
  const start = Array.from(before).length;
  const digest = "b".repeat(64);
  const pdf = {
    chunk_id: randomUUID(),
    index_id: randomUUID(),
    source_id: pdfId,
    filename: "边界.pdf",
    page: 2,
    record: null,
    record_id: null,
    text: quoted,
    start,
    end: start + Array.from(quoted).length,
    similarity: 0.8,
    content_hash: digest,
  };
  const csv = {
    ...pdf,
    chunk_id: randomUUID(),
    source_id: csvId,
    filename: "跨页.csv",
    page: null,
    record: 21,
    record_id: "F021",
    text: "第21条反馈",
    start: 0,
    end: 7,
  };
  const sources = [
    {
      id: pdfId,
      project_id: project.id,
      filename: pdf.filename,
      kind: "pdf",
      status: "ready",
      metadata_json: { page_count: 2 },
      size_bytes: 100,
      error: null,
    },
    {
      id: csvId,
      project_id: project.id,
      filename: csv.filename,
      kind: "csv",
      status: "ready",
      metadata_json: {
        row_count: 21,
        columns: [{ name: "text", dtype: "string", missing_count: 0 }],
      },
      size_bytes: 100,
      error: null,
    },
  ];
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  // 此用例仅模拟接口验证展示边界；实际模型链路由 analysis.spec.ts 独立覆盖。
  await page.route("**/sources", (route) => route.fulfill(json(sources)));
  await page.route("**/mapping", (route) =>
    route.fulfill(json({ fields: {} })),
  );
  await page.route("**/search", (route) =>
    route.fulfill(json({ results: [pdf, csv], notice: "模拟引用边界" })),
  );
  await page.route("**/citations/*", (route) =>
    route.fulfill(
      json(route.request().url().endsWith(pdf.chunk_id) ? pdf : csv),
    ),
  );
  await page.route("**/preview?*", (route) => {
    const url = new URL(route.request().url()),
      number = Number(url.searchParams.get("page"));
    expect(url.searchParams.get("expected_hash")).toBe(digest);
    return route.fulfill(
      json(
        url.pathname.includes(pdfId)
          ? {
              kind: "pdf",
              page: number,
              page_count: 2,
              text: number === 2 ? before + quoted + "\n结尾" : "其他页面",
            }
          : {
              kind: "csv",
              page: number,
              page_size: 20,
              total_rows: 21,
              columns: ["text"],
              rows:
                number === 2
                  ? [{ text: "第21条反馈" }]
                  : Array.from({ length: 20 }, (_, i) => ({
                      text: `第${i + 1}条反馈`,
                    })),
            },
      ),
    );
  });
  await page.goto(`/projects/${project.id}/analysis?tab=search`);
  await page.getByLabel("检索问题").fill("引用边界");
  await page.getByRole("button", { name: "检索证据" }).click();
  const buttons = page.getByRole("button", { name: "查看引用原文" });
  await buttons.first().click();
  const citation = page.getByRole("region", { name: "引用详情" });
  await expect(citation.locator("mark")).toHaveText(quoted);
  await expect(citation.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).badMarkup)).toBeUndefined();
  const visibleMark = await citation.locator(".pdf-text").evaluate((node) => {
    const mark = node.querySelector("mark")!.getBoundingClientRect(),
      box = node.getBoundingClientRect();
    return mark.top >= box.top && mark.bottom <= box.bottom;
  });
  expect(visibleMark).toBeTruthy();
  await citation.getByRole("button", { name: "上一页" }).click();
  await expect(citation.locator("mark")).toHaveCount(0);
  await citation.getByRole("button", { name: "关闭引用" }).click();
  await expect(buttons.first()).toBeFocused();
  await buttons.nth(1).click();
  await expect(citation.getByRole("row", { name: "引用对应记录" })).toHaveText(
    "第21条反馈",
  );
  await expect(citation).toContainText("第 2 / 2 页");
  await citation.getByRole("button", { name: "上一页" }).click();
  await expect(citation.locator(".citation-row")).toHaveCount(0);
});
