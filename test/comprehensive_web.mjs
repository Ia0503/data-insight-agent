// Check the prepared development project through its real UI; block all LLM submissions.
import { chromium, expect } from "./node_modules/@playwright/test/index.mjs";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const output = fileURLToPath(
  new URL("./results/comprehensive/", import.meta.url),
);
const state = JSON.parse(readFileSync(output + "project.json", "utf8"));
const expected = JSON.parse(
  readFileSync(
    new URL("./data/comprehensive/expected.json", import.meta.url),
    "utf8",
  ),
);
if (
  state.case_id !== expected.case_id ||
  !/^[0-9a-f-]{36}$/.test(state.project_id)
)
  throw new Error("Requires a prepared comprehensive acceptance project.");
const browser = await chromium.launch({ channel: "msedge" });
const context = await browser.newContext({
  baseURL: "http://127.0.0.1:5173",
  viewport: { width: 1440, height: 1000 },
});
let blockedSubmissions = 0;
await context.route("**/api/projects/**/agent/runs", async (route) => {
  if (route.request().method() === "POST") {
    blockedSubmissions++;
    await route.abort();
  } else await route.continue();
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const choose = async (label, option) => {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
};
const checkWidth = async () => {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
};
try {
  const project = `/projects/${state.project_id}`;
  await page.goto(project);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    expected.project_name,
  );
  const order = page
    .getByRole("row")
    .filter({ has: page.getByText("订单明细.csv", { exact: true }) });
  await expect(order).toContainText("2,440 行");
  await order.getByRole("button", { name: "查看预览", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "数据预览", exact: true }),
  ).toContainText("000001");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "数据预览", exact: true }),
  ).toContainText("000021");
  await page.screenshot({
    path: output + "workspace-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 320, height: 900 });
  await checkWidth();
  await page.screenshot({
    path: output + "workspace-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.goto(project + "/analysis");
  await choose("订单数据源", "订单明细.csv");
  await expect(
    page.getByRole("button", { name: "计算指标", exact: true }),
  ).toBeEnabled();
  await choose("指标", "净销售额");
  await choose("分组", "地区");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-09-01");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-09-30");
  await page
    .getByLabel("比较上一期（自然月或等天数）", { exact: true })
    .check();
  await page.getByRole("button", { name: "计算指标", exact: true }).click();
  await expect(page.locator(".metric-number")).toContainText("219375.00");
  await expect(page.locator(".analysis-result")).toContainText("-20.10%");
  for (const value of Object.values(expected.september_net_by_region))
    await expect(
      page.getByRole("table", { name: "指标分组结果", exact: true }),
    ).toContainText(value);
  await page.screenshot({
    path: output + "metrics-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 320, height: 900 });
  await checkWidth();
  await page.screenshot({
    path: output + "metrics-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("tab", { name: "文档索引", exact: true }).click();
  const indexPanel = page.getByRole("tabpanel", {
    name: "文档索引",
    exact: true,
  });
  await expect(indexPanel.getByRole("listitem")).toHaveCount(5);
  await expect(
    indexPanel.getByRole("listitem").filter({ hasText: "客户反馈.csv" }),
  ).toContainText("120 个片段");

  await page.goto(project + "/agent");
  await expect(page.getByRole("checkbox")).toHaveCount(8);
  for (const checkbox of await page.getByRole("checkbox").all())
    await checkbox.uncheck();
  await page.getByLabel("分析问题", { exact: true }).fill(state.main_question);
  for (const name of expected.main_sources)
    await page
      .locator(".agent-source-option")
      .filter({ hasText: name })
      .getByRole("checkbox")
      .check();
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    state.main_question,
  );
  await expect(page.locator(".agent-source-option input:checked")).toHaveCount(
    3,
  );
  await page.screenshot({ path: output + "agent-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 320, height: 900 });
  await checkWidth();
  await page.screenshot({ path: output + "agent-mobile.png", fullPage: true });
  expect(blockedSubmissions).toBe(0);
  expect(errors).toEqual([]);
  writeFileSync(
    output + "web.json",
    JSON.stringify(
      {
        workspace: true,
        pagination: true,
        actualMetric: "219375.00",
        comparison: "-20.10%",
        groups: true,
        draftRestored: true,
        mobile320: true,
        llmSubmissions: 0,
        errors,
      },
      null,
      2,
    ),
  );
  console.log(
    "Prepared project: workspace, pagination, actual metric, groups, draft and 320px UI passed; zero LLM submissions.",
  );
} catch (error) {
  await page
    .screenshot({
      path: output + `web-failure-${Date.now()}.png`,
      fullPage: true,
    })
    .catch(() => {});
  throw error;
} finally {
  await context.close();
  await browser.close();
}
