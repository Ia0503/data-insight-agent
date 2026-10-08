import { test, expect } from "@playwright/test";
import { fileURLToPath } from "node:url";

const fixture = (name: string) =>
  fileURLToPath(new URL(`../data/demo/${name}`, import.meta.url));

test("create, edit, upload CSV/PDF, paginate, refresh, and inspect failed upload", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  const name = `SaaS 浏览器验证 ${Date.now()}(test)`;
  await page.getByRole("textbox", { name: "项目名称", exact: true }).fill(name);
  await page
    .getByRole("textbox", { name: "项目描述", exact: true })
    .fill("销售与版本资料导入验证");
  await page.getByRole("button", { name: "保存项目" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("button", { name: "编辑项目" }).click();
  await page
    .getByRole("textbox", { name: "项目描述", exact: true })
    .fill("更新后的描述");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByText("更新后的描述", { exact: true })).toBeVisible();
  await page.getByLabel("选择上传文件").setInputFiles(fixture("sales.csv"));
  await expect(page.getByRole("heading", { name: "sales.csv" })).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "1000", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "空值", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText("sales.csv", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看预览" }).click();
  await expect(page.getByRole("heading", { name: "sales.csv" })).toBeVisible();
  await page
    .getByLabel("选择上传文件")
    .setInputFiles(fixture("quarterly_report.pdf"));
  await expect(
    page.getByRole("heading", { name: "quarterly_report.pdf" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".pdf-text")).toContainText("Version 3.2");
  await page
    .getByLabel("选择上传文件")
    .setInputFiles(fixture("duplicate_columns.csv"));
  await expect(
    page.getByText("CSV 存在重复列名，请修正后上传。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    page.getByText("CSV 存在重复列名，请修正后上传。"),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: fileURLToPath(new URL("../results/workspace.png", import.meta.url)),
    fullPage: true,
  });
  expect(browserErrors).toEqual([]);
});

test("failed API request shows error instead of creating a project", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page
    .getByRole("textbox", { name: "项目名称", exact: true })
    .fill("Network failure");
  await page.route("**/api/projects", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "模拟服务不可用" }),
        })
      : route.continue(),
  );
  await page.getByRole("button", { name: "保存项目" }).click();
  await expect(page.getByRole("alert")).toContainText("模拟服务不可用");
  await expect(page).toHaveURL("http://127.0.0.1:5174/");
});
