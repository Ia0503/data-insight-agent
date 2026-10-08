// 只读核对主容器入口与已有项目，不提交模型任务，不记录 trace。
import { chromium, expect } from "../node_modules/@playwright/test/index.mjs";
import { readFileSync, mkdirSync, existsSync, writeFileSync } from "node:fs";
import { resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const [manifestFile, destination] = process.argv.slice(2);
if (!manifestFile || !destination)
  throw new Error("Manifest and output required.");
const output = resolve(root, destination);
const within = relative(resolve(root, "test/results"), output);
const manifest = resolve(root, manifestFile);
const input = relative(resolve(root, "test/results"), manifest);
if (
  !within ||
  within.startsWith("..") ||
  isAbsolute(within) ||
  existsSync(output) ||
  !input ||
  input.startsWith("..") ||
  isAbsolute(input)
)
  throw new Error("Use an ignored manifest and new output under test/results.");
mkdirSync(output, { recursive: true });
const projects = JSON.parse(
  readFileSync(manifest, "utf8").replace(/^\uFEFF/, ""),
).projects;
const browser = await chromium.launch({
  channel: process.env.E2E_CHANNEL || "msedge",
});
const context = await browser.newContext({ baseURL: "http://127.0.0.1:8080" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const health = await context.request.get("/api/health");
  expect(health.ok()).toBe(true);
  expect((await health.json()).database).toBe("connected");
  await page.goto("/");
  for (const project of projects)
    await expect(page.getByText(project.name, { exact: true })).toBeVisible();
  const project =
    projects.find((item) => item.name.includes("综合")) || projects[0];
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/projects/${project.id}/agent`);
    await expect(page.getByLabel("调用密码")).toHaveValue("");
    await expect(
      page.getByRole("button", { name: "开始分析", exact: true }),
    ).toBeDisabled();
    await expect(page.locator(".quota-status")).toContainText("1,000,000");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: resolve(output, `analysis-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("link", { name: "历史分析", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: project.name, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "查询历史", exact: true }),
    ).toBeVisible();
  }
  expect(errors).toEqual([]);
  writeFileSync(
    resolve(output, "verification.json"),
    JSON.stringify(
      {
        passed: true,
        projects: projects.length,
        widths: [1440, 320],
        llm_requests: 0,
        page_errors: errors,
      },
      null,
      2,
    ),
  );
  console.log("Primary container webpage read-only checks passed.");
} finally {
  await browser.close();
}
