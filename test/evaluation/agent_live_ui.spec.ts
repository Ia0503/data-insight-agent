import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { resolve, relative, isAbsolute } from "node:path";

// 只读取已完成的真实报告；测试后端仍禁用模型，不额外触发付费请求。
const resultName = process.env.AGENT_LIVE_RESULT ?? "results.json";
if (
  !["results.json", "results-normal.json", "verified-normal.json"].includes(
    resultName,
  )
)
  throw new Error("Only saved normal evaluation artifacts are accepted.");
const resultsRoot = fileURLToPath(new URL("../results/", import.meta.url));
const outputDir = process.env.AGENT_LIVE_DIR
  ? resolve(process.env.AGENT_LIVE_DIR)
  : resolve(resultsRoot, "agent-live");
const withinResults = relative(resultsRoot, outputDir);
if (withinResults.startsWith("..") || isAbsolute(withinResults))
  throw new Error("Saved report artifacts must be inside test/results.");
const results = JSON.parse(
  readFileSync(resolve(outputDir, resultName), "utf8"),
);
const normal = results.cases.find(
  (value: { case: string }) => value.case === "normal",
);

test("completed real run survives restart, idempotency and scope checks", async ({
  request,
}) => {
  const path = `/api/projects/${normal.project_id}/agent`;
  const config = await (await request.get(path + "/configuration")).json();
  expect(config.enabled).toBe(false);
  const current = await (
    await request.get(path + `/runs/${normal.run.id}`)
  ).json();
  expect(current.report).toEqual(normal.run.report);
  const repeated = await request.post(path + "/runs", {
    data: {
      request_id: current.request_id,
      question: current.question,
      source_ids: current.snapshot.sources.map(
        (source: { id: string }) => source.id,
      ),
    },
  });
  expect(repeated.status()).toBe(202);
  expect((await repeated.json()).id).toBe(current.id);
  expect((await repeated.json()).usage).toEqual(current.usage);
  const other = await (
    await request.post("/api/projects", {
      data: { name: "真实联调跨项目隔离检查(test)" },
    })
  ).json();
  for (const suffix of ["", "/events"])
    expect(
      (
        await request.get(
          `/api/projects/${other.id}/agent/runs/${current.id}${suffix}`,
        )
      ).status(),
    ).toBe(404);
  const disabled = await request.post(path + "/runs", {
    data: {
      request_id: randomUUID(),
      question: "此请求应被关闭的调用开关拒绝。",
      source_ids: current.snapshot.sources.map(
        (source: { id: string }) => source.id,
      ),
    },
  });
  expect(disabled.status()).toBe(503);
});

for (const width of [1440, 320]) {
  test(`real Agent report and original citations at ${width}px`, async ({
    page,
  }) => {
    expect(normal.passed).toBe(true);
    const errors: string[] = [];
    const requests: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/agent/runs"))
        requests.push(request.url());
    });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      `/projects/${normal.project_id}/agent/runs/${normal.run.id}`,
    );
    await expect(page.locator(".agent-run-status")).toContainText("已完成");
    await expect(page.locator(".agent-report")).toContainText("76000.00");
    await expect(page.locator(".agent-report")).toContainText("-24.00%");
    await expect(page.locator(".agent-report")).toContainText("80000.00");
    if (normal.run.report.version === "agent-report-v2") {
      if (
        normal.run.report.findings.some(
          (finding: { category?: string }) =>
            finding.category === "document_quote",
        )
      )
        await expect(
          page
            .locator(".agent-finding-label")
            .filter({ hasText: "原文摘录" })
            .first(),
        ).toBeVisible();
      const previous = normal.run.report.sources.find(
        (source: { id: string }) => source.id.endsWith(":previous"),
      );
      if (previous) {
        const previousRow = page
          .locator(".agent-evidence li")
          .filter({ hasText: `· ${previous.id}` })
          .first();
        await previousRow.getByRole("button", { name: "查看原文" }).click();
        await expect(page.locator("tr.citation-row")).toContainText("0001");
        await page
          .locator(".agent-preview > .section-heading")
          .getByRole("button", { name: "返回分析报告", exact: true })
          .filter({ visible: true })
          .click();
      }
    }
    await expect(
      page.getByRole("button", { name: "用此问题新建分析", exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "执行过程", exact: true }).click();
    await expect(page.locator(".agent-progress")).toContainText(
      "报告结构、数值来源和引用已校验",
    );
    await page.getByRole("link", { name: "分析报告", exact: true }).click();
    const csv = normal.run.report.sources.find(
      (source: { kind: string; records?: number[] }) =>
        source.kind === "csv" && source.records?.[0] === 5,
    );
    expect(csv).toBeTruthy();
    const csvRow = page
      .locator(".agent-evidence li")
      .filter({ hasText: `· ${csv.id}` })
      .filter({ hasText: csv.filename })
      .first();
    const csvButton = csvRow.getByRole("button", { name: "查看原文" });
    await csvButton.click();
    await expect(csvRow).toHaveClass(/selected/);
    await expect(page.locator("tr.citation-row")).toContainText("0005");
    await page
      .locator(".agent-preview > .section-heading")
      .getByRole("button", { name: "返回分析报告", exact: true })
      .filter({ visible: true })
      .click();
    await expect(csvButton).toBeFocused();
    const document = normal.run.report.sources.find(
      (source: { kind: string; page?: number }) =>
        source.kind === "document" && source.page,
    );
    expect(document).toBeTruthy();
    const docRow = page
      .locator(".agent-evidence li")
      .filter({ hasText: `· ${document.id}` })
      .first();
    await docRow.getByRole("button", { name: "查看原文" }).click();
    await expect(docRow).toHaveClass(/selected/);
    await expect(page.locator("mark.citation-highlight")).toHaveText(
      document.text,
    );
    await expect(page.locator(".agent-preview")).toContainText(
      `第 ${document.page} /`,
    );
    await page.screenshot({
      path: `${outputDir}/report${normal.run.report.version === "agent-report-v2" ? "-v2" : ""}-${width}.png`,
      fullPage: true,
    });
    await page
      .locator(".agent-preview > .section-heading")
      .getByRole("button", { name: "返回分析报告", exact: true })
      .filter({ visible: true })
      .click();
    if (
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      )
    )
      writeFileSync(
        `${outputDir}/step4-overflow-${width}.json`,
        JSON.stringify(
          await page.evaluate(() =>
            [...document.querySelectorAll("body *")]
              .map((node) => ({
                tag: node.tagName,
                class: String(node.className),
                text: node.textContent?.slice(0, 100),
                width: node.getBoundingClientRect().width,
                right: node.getBoundingClientRect().right,
              }))
              .filter((v) => v.right > innerWidth),
          ),
          null,
          2,
        ),
      );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(errors).toEqual([]);
    expect(requests).toEqual([]);
  });
}
