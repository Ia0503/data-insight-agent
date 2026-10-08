// 显式验证隔离容器网页：真实计费，无 trace，无任务自动重试。
import { chromium, expect } from "../node_modules/@playwright/test/index.mjs";
import { mkdirSync, existsSync, writeFileSync, readFileSync } from "node:fs";
import { resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const [mode, destination, smokeFile] = process.argv.slice(2);
if (mode !== "--run" || !destination || !smokeFile)
  throw new Error(
    "Requires explicit --run and a new output directory under test/results.",
  );
const output = resolve(root, destination);
const within = relative(resolve(root, "test/results"), output);
if (
  !within ||
  within.startsWith("..") ||
  isAbsolute(within) ||
  existsSync(output)
)
  throw new Error("Output must be a new directory under test/results.");
mkdirSync(output, { recursive: true });
const smokePath = resolve(root, smokeFile);
if (
  !smokePath.startsWith(resolve(root, "test/results") + "/") &&
  !smokePath.startsWith(resolve(root, "test/results") + "\\")
)
  throw new Error("Use an ignored isolated smoke manifest.");
const projectId = JSON.parse(readFileSync(smokePath, "utf8")).project_id;
const projectPath = `/api/projects/${projectId}`;
const question =
  "使用订单表已保存的字段映射，比较二〇二六年九月与八月的净销售额，并将九月净销售额按地区分组。检索用户反馈及季度报告中的新版本闪退与退款情况。请给出指标和图表，区分计算事实、资料摘录、推断及未知，不把反馈直接认定为销售下降的原因。";
const browser = await chromium.launch({ channel: "msedge" });
const context = await browser.newContext({
  baseURL: "http://127.0.0.1:8082",
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const request = context.request;
const errors = [];
let submissions = 0;
let streams = 0;
let runId;
let terminal = false;
let run;
const fetchJson = async (path) => {
  const response = await request.get(path);
  expect(response.ok()).toBe(true);
  return response.json();
};
const save = (name, value) =>
  writeFileSync(resolve(output, name), JSON.stringify(value, null, 2));
page.on("pageerror", (error) => errors.push(error.message));
page.on("request", (value) => {
  if (value.method() === "POST" && value.url().endsWith("/agent/runs"))
    submissions++;
});
page.on("response", (response) => {
  if (response.url().includes("/stream?") && response.status() === 200)
    streams++;
});
try {
  const project = await fetchJson(projectPath);
  expect(project.name).toBe("容器部署验收(test)");
  const configuration = await fetchJson(projectPath + "/agent/configuration");
  expect(configuration.configured && configuration.enabled).toBe(true);
  expect(configuration.provider).toBe("aliyun");
  expect(
    configuration.password_required && configuration.access_configured,
  ).toBe(true);
  const sources = await fetchJson(projectPath + "/sources");
  const chosen = ["orders.csv", "feedback.csv", "quarterly_report.pdf"];
  for (const filename of chosen)
    expect(sources.find((source) => source.filename === filename)?.status).toBe(
      "ready",
    );
  const deniedBody = {
    question,
    source_ids: sources.map((source) => source.id),
    request_id: crypto.randomUUID(),
  };
  expect(
    (
      await request.post(projectPath + "/agent/runs", { data: deniedBody })
    ).status(),
  ).toBe(401);
  expect(
    (
      await request.post(projectPath + "/agent/runs", {
        data: deniedBody,
        headers: { "X-Analysis-Password": "wrong-test-only-password" },
      })
    ).status(),
  ).toBe(401);
  await page.goto(`/projects/${projectId}/agent`);
  await page.getByRole("textbox", { name: "分析问题" }).fill(question);
  for (const source of sources)
    await page
      .locator(".agent-source-option")
      .filter({ hasText: source.filename })
      .getByRole("checkbox")
      .setChecked(chosen.includes(source.filename));
  const submit = page.getByRole("button", { name: "开始分析", exact: true });
  await expect(submit).toBeDisabled();
  await page.getByLabel("调用密码").fill("test-only-call-password");
  await expect(submit).toBeEnabled();
  await page.screenshot({ path: resolve(output, "ready.png"), fullPage: true });
  await submit.click();
  await expect(page).toHaveURL(/\/agent\/runs\/[a-f0-9-]+$/, {
    timeout: 15000,
  });
  runId = new URL(page.url()).pathname.split("/").at(-1);
  const runPath = projectPath + "/agent/runs/" + runId;
  console.log("Submitted one real task through Docker and Nginx: " + runId);
  await expect(page.locator(".agent-run-status")).toContainText("实时连接", {
    timeout: 10000,
  });
  await expect(page.locator(".agent-progress")).toContainText("请求模型", {
    timeout: 15000,
  });
  await page.reload();
  await expect(page.locator(".agent-run-status")).toContainText("实时连接", {
    timeout: 10000,
  });
  await expect
    .poll(
      async () => {
        run = await fetchJson(runPath);
        return run.status;
      },
      { timeout: 310000, intervals: [1000, 2000] },
    )
    .not.toMatch(/^(queued|running)$/);
  terminal = true;
  const events = await fetchJson(runPath + "/events");
  save("run.json", {
    checked_at: new Date().toISOString(),
    project_id: projectId,
    run,
    events,
  });
  expect(run.status, run.error ?? "real analysis should succeed").toBe(
    "succeeded",
  );
  expect(run.provider).toBe("aliyun");
  expect(run.usage.total_tokens).toBeGreaterThan(0);
  expect(run.usage.model_calls).toBeGreaterThan(0);
  expect(run.usage.model_calls).toBeLessThanOrEqual(16);
  const afterConfig = await fetchJson(projectPath + "/agent/configuration");
  expect(afterConfig.quota.reserved).toBe(0);
  if (afterConfig.quota.reset_at === configuration.quota.reset_at)
    expect(afterConfig.quota.used - configuration.quota.used).toBe(
      run.usage.total_tokens,
    );
  const net = run.report.metrics.find(
    (metric) =>
      metric.metric === "net_sales" && metric.filters.start === "2026-09-01",
  );
  expect(net?.value).toBe("76000.00");
  expect(net?.comparison?.value).toBe("100000.00");
  expect(net?.comparison?.change_percent).toBe("-24.00");
  expect(run.report.chart_specs.length).toBeGreaterThan(0);
  await page.getByRole("link", { name: "分析报告", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: run.report.title, exact: true }),
  ).toBeVisible();
  await expect(page.locator(".chart-canvas svg").first()).toBeVisible();
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    const csv = run.report.sources.find(
      (source) => source.kind === "csv" && source.records?.[0] === 5,
    );
    expect(csv).toBeTruthy();
    const row = page
      .locator(".agent-evidence li")
      .filter({ hasText: `· ${csv.id}` })
      .first();
    const original = row.getByRole("button", { name: "查看原文" });
    await original.click();
    await expect(row).toHaveClass(/selected/);
    await expect(page.locator("tr.citation-row")).toContainText("0005");
    await page
      .locator(".agent-preview > .section-heading")
      .getByRole("button", { name: "返回分析报告", exact: true })
      .filter({ visible: true })
      .click();
    await expect(original).toBeFocused();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: resolve(output, `report-${width}.png`),
      fullPage: true,
    });
  }
  await page.reload();
  await expect(
    page.getByRole("heading", { name: run.report.title, exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "执行过程", exact: true }).click();
  await expect(page.locator(".agent-progress")).toContainText(
    "报告结构、数值来源和引用已校验",
  );
  await expect(page.locator(".run-statistics")).toContainText(
    String(run.usage.total_tokens),
  );
  await page.getByRole("link", { name: "历史分析", exact: true }).click();
  await expect(page.locator(".history-list")).toContainText(run.report.title);
  expect(submissions).toBe(1);
  expect(streams).toBeGreaterThanOrEqual(2);
  expect(errors).toEqual([]);
  save("verification.json", {
    passed: true,
    checked_at: new Date().toISOString(),
    run_id: runId,
    report_url: `http://localhost:8082/projects/${projectId}/agent/runs/${runId}`,
    configuration,
    usage: run.usage,
    tool_count: run.tool_count,
    browser_submissions: submissions,
    streams,
    page_errors: errors,
    checks: [
      "actual isolated Docker services",
      "single real webpage submission",
      "running refresh and stream recovery",
      "hand-calculated monthly nets",
      "SVG chart",
      "1440/320px CSV evidence and focus",
      "saved report reload",
      "execution usage",
      "history",
    ],
  });
  console.log(
    JSON.stringify({
      passed: true,
      run_id: runId,
      usage: run.usage,
      tool_count: run.tool_count,
    }),
  );
} catch (error) {
  // 失败只保存公共状态与受控诊断；不重发任务，不记录模型原始响应。
  if (runId && !terminal) {
    await request.post(projectPath + "/agent/runs/" + runId + "/cancel");
    run = await fetchJson(projectPath + "/agent/runs/" + runId);
    save("run.json", { project_id: projectId, run });
  }
  save("verification.json", {
    passed: false,
    run_id: runId,
    status: run?.status,
    error: run?.error,
    browser_submissions: submissions,
    streams,
    page_errors: errors,
  });
  throw error;
} finally {
  await browser.close();
}
