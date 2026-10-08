import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const fixture = (name: string) => fileURLToPath(new URL(`../data/business/${name}`, import.meta.url));
async function select(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}
async function prepare(request: APIRequestContext, names: string[]) {
  const project = await (await request.post("http://127.0.0.1:8001/api/projects", { data: { name: `步骤二浏览器验证 ${Date.now()}(test)` } })).json();
  const sources: Record<string, string> = {};
  for (const name of names) {
    const response = await request.post(`http://127.0.0.1:8001/api/projects/${project.id}/sources`, { multipart: { file: { name, mimeType: "application/octet-stream", buffer: await readFile(fixture(name)) } } });
    expect(response.ok()).toBeTruthy();
    sources[name] = (await response.json()).id;
  }
  return { project, sources };
}

test("business mapping, metrics, saved state and controlled tools", async ({ page, request }) => {
  const { project } = await prepare(request, ["orders.csv"]);
  await page.goto(`/projects/${project.id}`);
  await page.getByRole("link", { name: "分析与检索", exact: true }).click();
  const detailLinks = page.getByRole("link", { name: "项目详情", exact: true });
  await expect(detailLinks).toHaveCount(2);
  for (const link of await detailLinks.all()) await expect(link).toHaveAttribute("href", `/projects/${project.id}`);
  await expect(page.getByRole("button", { name: "计算指标" })).toBeDisabled();
  const fields = { "订单编号": "order_id", "支付日期": "paid_at", "实付金额（元）": "paid_amount", "累计退款（元）": "refund_amount", "地区": "region", "产品": "product", "订单状态": "status" };
  for (const [label, column] of Object.entries(fields)) await select(page, label, column);
  await page.getByRole("button", { name: "校验并保存映射" }).click();
  await expect(page.getByText("字段映射已校验并保存。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "计算指标" }).click();
  await expect(page.locator(".metric-number")).toContainText("76000.00");
  await expect(page.locator(".analysis-result")).toContainText("-24.00%");
  await expect(page.getByRole("table", { name: "指标分组结果" })).toContainText("39000.00");
  await page.getByText("计算依据与版本", { exact: true }).click();
  await expect(page.getByText(/记录序号：5、6、7、8/)).toBeVisible();
  await page.getByText("通用筛选与分组工具", { exact: true }).click();
  await select(page, "等值筛选列", "order_id");
  await page.getByLabel("筛选值", { exact: true }).fill("0005");
  await page.getByRole("button", { name: "运行工具" }).click();
  await expect(page.getByRole("table", { name: "通用工具结果" })).toContainText("0005");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "订单编号", exact: true })).toContainText("order_id");
  await expect(page.getByRole("button", { name: "计算指标" })).toBeEnabled();
  await select(page, "订单编号", "region");
  await expect(page.getByRole("button", { name: "计算指标" })).toBeDisabled();
  await page.screenshot({ path: fileURLToPath(new URL("../results/step2-analysis-desktop.png", import.meta.url)), fullPage: true });
});

test("real BGE indexing, search and page citation on mobile", async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  const { project } = await prepare(request, ["quarterly_report.pdf", "feedback.csv"]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects/${project.id}/analysis`);
  await page.getByRole("tab", { name: "文档索引", exact: true }).click();
  await page.getByRole("button", { name: "建立索引", exact: true }).click();
  await expect(page.locator(".index-list")).toContainText("已就绪", { timeout: 60_000 });
  await page.getByRole("tab", { name: "证据检索", exact: true }).click();
  await page.getByLabel("检索问题").fill("新版本崩溃闪退，有人要求退款吗？");
  await page.getByRole("button", { name: "检索证据" }).click();
  await expect(page.locator(".search-results")).toContainText("第2页");
  await page.getByRole("button", { name: "查看引用原文" }).first().click();
  const citation = page.getByRole("region", { name: "引用详情" });
  await expect(citation).toContainText("第2页");
  await expect(citation.locator(".pdf-text")).toContainText("不能直接证明");
  await expect(citation.locator(".citation-highlight")).toContainText("不能直接证明");
  await expect(page.locator(".selected-evidence")).toHaveCount(1);
  expect(await page.locator(".similarity-score").first().evaluate(node => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  const heights = await page.locator(".analysis-panel button:visible").evaluateAll(buttons => buttons.map(b => b.getBoundingClientRect().height));
  expect(heights.every(h => h >= 44)).toBeTruthy();
  await page.screenshot({ path: fileURLToPath(new URL("../results/step2-analysis-mobile.png", import.meta.url)), fullPage: true });
  await page.getByRole("button", { name: "关闭引用" }).click();
  await page.getByRole("tab", { name: "文档索引", exact: true }).click();
  await select(page, "索引数据源", "feedback.csv");
  await select(page, "反馈文本列", "text");
  await select(page, "反馈编号列", "feedback_id");
  await page.getByRole("button", { name: "建立索引", exact: true }).click();
  await expect(page.locator(".index-list")).toContainText("6 个片段", { timeout: 30_000 });
  await page.getByRole("tab", { name: "证据检索", exact: true }).click();
  await select(page, "检索范围", "feedback.csv");
  await page.getByLabel("检索问题").fill("账户无法登录一直在加载");
  await page.getByRole("button", { name: "检索证据" }).click();
  await page.getByRole("button", { name: "查看引用原文" }).first().click();
  await expect(citation).toContainText("F002");
  await expect(citation).toContainText("登录一直转圈");
  await expect(citation.getByRole("row", { name: "引用对应记录" })).toContainText("F002");
  expect(errors).toEqual([]);
});

test("search failure recovers and late result cannot overwrite another project", async ({ page, request }) => {
  const first = await prepare(request, []), second = await prepare(request, []);
  await page.goto(`/projects/${first.project.id}/analysis?tab=search`);
  await page.route("**/search", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "模拟模型不可用" }) }));
  await page.getByLabel("检索问题").fill("测试");
  await page.getByRole("button", { name: "检索证据" }).click();
  await expect(page.getByRole("alert")).toContainText("模拟模型不可用");
  await page.unroute("**/search");
  let finish!: () => void;
  const ready = new Promise<void>(resolve => finish = resolve);
  let release!: () => void;
  const blocked = new Promise<void>(resolve => release = resolve);
  await page.route("**/search", async route => { finish(); await blocked; await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: [], reason: "旧项目查询结果" }) }); });
  await page.getByRole("button", { name: "检索证据" }).click();
  await ready;
  await page.evaluate(async id => { const router = (await import("/src/router.ts")).default; await router.push(`/projects/${id}/analysis?tab=search`); }, second.project.id);
  release();
  await expect(page.getByRole("heading", { name: second.project.name, exact: true })).toBeVisible();
  await expect(page.getByText("旧项目查询结果", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "检索证据" })).toBeEnabled();
});


test("analysis tabs retain drafts and dropdown supports keyboard and validation", async ({ page, request }) => {
  const { project } = await prepare(request, ["orders.csv"]);
  await page.goto(`/projects/${project.id}/analysis`);
  await expect(page.getByRole("tabpanel", { name: "分析任务" })).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: "证据检索" })).toBeHidden();
  await expect(page.getByRole("combobox", { name: "订单编号", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "校验并保存映射" }).click();
  await expect(page.getByRole("listbox")).toHaveCount(1);
  const field = page.getByRole("combobox", { name: "订单编号", exact: true });
  await expect(field).toBeFocused();
  await field.press("Escape");
  await field.press("ArrowDown");
  await field.press("o");
  await field.press("Enter");
  await expect(field).toContainText("order_id");
  await field.click();
  await field.press("End");
  await field.press("Escape");
  await expect(field).toContainText("order_id");
  await field.click();
  await page.getByRole("heading", { name: "业务指标", exact: true }).click();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await page.locator("label").filter({ has: field }).click({ position: { x: 12, y: 6 } });
  await expect(field).toHaveAttribute("aria-expanded", "true");
  await field.press("Escape");
  await page.getByLabel("地区筛选").fill("华东");
  await page.getByRole("tab", { name: "分析任务" }).press("ArrowRight");
  await expect(page.getByRole("tab", { name: "证据检索" })).toBeFocused();
  await expect(page).toHaveURL(/tab=search/);
  await page.getByLabel("检索问题").fill("保留问题草稿");
  await page.getByRole("tab", { name: "文档索引" }).click();
  await expect(page.getByRole("tabpanel", { name: "证据检索" })).toBeHidden();
  await page.getByRole("tab", { name: "分析任务" }).click();
  await expect(field).toContainText("order_id");
  await expect(page.getByLabel("地区筛选")).toHaveValue("华东");
  await page.goBack();
  await expect(page.getByRole("tab", { name: "文档索引" })).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page.getByRole("tabpanel", { name: "文档索引" })).toBeVisible();
  await page.getByRole("tab", { name: "证据检索" }).click();
  await page.getByRole("combobox", { name: "检索范围" }).click();
  await page.screenshot({ path: fileURLToPath(new URL("../results/analysis-dropdown-desktop.png", import.meta.url)) });
});

test("search controls align and navigation stays usable at narrow widths", async ({ page, request }) => {
  const { project } = await prepare(request, ["orders.csv"]);
  await page.goto(`/projects/${project.id}/analysis?tab=search`);
  const scope = page.getByRole("combobox", { name: "检索范围" });
  const threshold = page.getByLabel("最低相似度");
  const scopeBox = await scope.boundingBox(), thresholdBox = await threshold.boundingBox();
  expect(Math.abs(scopeBox!.y - thresholdBox!.y)).toBeLessThan(1);
  expect(Math.abs(scopeBox!.height - thresholdBox!.height)).toBeLessThan(1);
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await scope.click();
    await expect(page.getByRole("option", { name: "当前项目全部已索引文件", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    const controls = await page.locator('[role="tab"], [role="option"], [role="combobox"]').evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().height > 0).map(n => n.getBoundingClientRect().height));
    expect(controls.every(height => height >= 44)).toBeTruthy();
    await scope.press("Escape");
  }
  await page.getByRole("tab", { name: "分析任务" }).click();
  const checkbox = page.getByRole("checkbox");
  await expect(checkbox).toBeVisible();
  expect(await checkbox.evaluate(node => getComputedStyle(node).accentColor)).toBe("rgb(188, 43, 33)");
});
