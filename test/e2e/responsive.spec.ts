import { test, expect, type Locator, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const fixture = (name: string) =>
  fileURLToPath(new URL(`../data/demo/${name}`, import.meta.url));
const screenshot = (name: string) =>
  fileURLToPath(new URL(`../results/${name}.png`, import.meta.url));
const longName = "客户反馈与销售经营分析：长项目名称和文件名称的窄屏验证(test)";
const csvName = "客户反馈与销售数据_较长的中文文件名称.csv";
async function noPageOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}
async function touchControl(control: Locator) {
  await expect(control).toBeVisible();
  expect(
    await control.evaluate((node) => {
      const bounds = node.getBoundingClientRect();
      return (
        bounds.height >= 44 &&
        bounds.left >= 0 &&
        bounds.right <= window.innerWidth
      );
    }),
  ).toBe(true);
}

test.describe("mobile task layout", () => {
  test.use({ hasTouch: true, reducedMotion: "reduce" });
  for (const width of [320, 375, 390, 430, 768]) {
    test(`${width}px: forms, source actions, preview navigation and downloads`, async ({
      page,
      request,
    }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.setViewportSize({ width, height: 844 });
      await page.emulateMedia({
        reducedMotion: width === 390 ? "no-preference" : "reduce",
      });
      await page.goto("/");
      await touchControl(
        page.getByRole("button", { name: "创建项目", exact: true }),
      );
      await page.getByRole("button", { name: "创建项目", exact: true }).tap();
      await page
        .getByRole("textbox", { name: "项目名称", exact: true })
        .fill(longName);
      await page
        .getByRole("textbox", { name: "项目描述", exact: true })
        .fill("窄屏上传与预览验证");
      await noPageOverflow(page);
      await page.getByRole("button", { name: "保存项目" }).tap();
      await expect(
        page.getByRole("heading", { name: longName, exact: true }),
      ).toBeVisible();
      await touchControl(page.getByRole("button", { name: "编辑项目" }));
      await page.getByRole("button", { name: "编辑项目" }).tap();
      await page
        .getByRole("textbox", { name: "项目描述", exact: true })
        .fill("检查文件操作无需横向滑动、预览定位和返回。");
      await page.getByRole("button", { name: "保存修改" }).tap();
      await expect(
        page.getByText("检查文件操作无需横向滑动、预览定位和返回。", {
          exact: true,
        }),
      ).toBeVisible();
      await page.getByLabel("选择上传文件").setInputFiles({
        name: csvName,
        mimeType: "text/csv",
        buffer: Buffer.from(
          "date,region,revenue,orders,segment,currency\n2026-09-01,East,1000,10,Enterprise,CNY\n",
        ),
      });
      await expect(
        page.getByRole("table", { name: "数据预览", exact: true }),
      ).toBeVisible();
      await page
        .getByLabel("选择上传文件")
        .setInputFiles(fixture("quarterly_report.pdf"));
      await expect(
        page.getByRole("heading", { name: "quarterly_report.pdf" }),
      ).toBeVisible();
      await page
        .getByLabel("选择上传文件")
        .setInputFiles(fixture("duplicate_columns.csv"));
      await expect(
        page.getByText("CSV 存在重复列名，请修正后上传。", { exact: true }),
      ).toBeVisible();
      const sourceList = page.getByRole("table", {
        name: "数据源列表",
        exact: true,
      });
      expect(
        await page
          .locator(".list-scroll")
          .evaluate((node) => node.scrollWidth <= node.clientWidth),
      ).toBe(true);
      await touchControl(
        sourceList.getByRole("button", { name: "重试", exact: true }),
      );
      await sourceList.getByRole("button", { name: "重试", exact: true }).tap();
      await expect(
        page.getByText("CSV 存在重复列名，请修正后上传。", { exact: true }),
      ).toBeVisible();
      const csvRow = sourceList.getByRole("row").filter({ hasText: csvName });
      const previewButton = csvRow.getByRole("button", {
        name: "查看预览",
        exact: true,
      });
      await touchControl(previewButton);
      await previewButton.tap();
      const target = page.getByRole("region", {
        name: "当前文件预览",
        exact: true,
      });
      await expect(target).toBeFocused();
      await expect(
        page.getByRole("heading", { name: csvName, exact: true }),
      ).toBeInViewport();
      const download = page.getByRole("link", {
        name: "下载原文件",
        exact: true,
      });
      await touchControl(download);
      const downloadEvent = page.waitForEvent("download");
      await download.tap();
      expect((await downloadEvent).suggestedFilename()).toBe(csvName);
      await page.getByText("字段信息", { exact: true }).tap();
      await expect(
        page.getByRole("table", { name: "字段信息", exact: true }),
      ).toBeVisible();
      const dataRegion = page.getByRole("region", {
        name: "数据预览滚动区域",
        exact: true,
      });
      if (width < 768) {
        await expect(
          page.getByText("可左右滑动查看其余列", { exact: true }),
        ).toBeVisible();
        await dataRegion.evaluate((node) => {
          node.scrollLeft = node.scrollWidth;
        });
        expect(await dataRegion.evaluate((node) => node.scrollLeft > 0)).toBe(
          true,
        );
      } else {
        await expect(
          page.getByText("可左右滑动查看其余列", { exact: true }),
        ).toBeHidden();
      }
      await noPageOverflow(page);
      await page.getByRole("button", { name: "返回数据源", exact: true }).tap();
      await expect(
        page.getByRole("heading", { name: "数据源 3" }),
      ).toBeInViewport();
      await expect(page.locator(".sources-section")).toBeFocused();
      await page.screenshot({
        path: screenshot(`mobile-sources-${width}`),
        fullPage: true,
      });
      await sourceList
        .getByRole("row")
        .filter({ hasText: "quarterly_report.pdf" })
        .getByRole("button", { name: "查看预览" })
        .tap();
      await expect(target).toBeFocused();
      await touchControl(
        page.getByRole("button", { name: "下一页", exact: true }),
      );
      await page.getByRole("button", { name: "下一页", exact: true }).tap();
      await expect(page.locator(".pdf-text")).toContainText("Version 3.2");
      await noPageOverflow(page);
      const projectId = page.url().split("/").pop();
      const current = await (
        await request.get(`/api/projects/${projectId}`)
      ).json();
      const otherResponse = await request.post("/api/projects", {
        data: { name: "SaaS 数据导入示例(test)", description: "中英文混排对照" },
      });
      expect(otherResponse.ok()).toBeTruthy();
      const other = await otherResponse.json();
      await page.route("**/api/projects", (route) =>
        route.fulfill({ json: [other, current] }),
      );
      await page
        .getByRole("navigation", { name: "主导航" })
        .getByRole("link", { name: "项目", exact: true })
        .tap();
      await expect(
        page.getByRole("link", { name: `打开项目：${longName}` }),
      ).toBeVisible();
      const fonts = await page.locator(".project-name a").evaluateAll((nodes) =>
        nodes.map((node) => {
          const style = getComputedStyle(node);
          return [
            style.fontFamily,
            style.fontSize,
            style.fontWeight,
            style.lineHeight,
          ];
        }),
      );
      expect(fonts).toHaveLength(2);
      expect(fonts[0]).toEqual(fonts[1]);
      for (const button of await page.locator(".project-table .button").all())
        await touchControl(button);
      await noPageOverflow(page);
      await page.screenshot({
        path: screenshot(`mobile-projects-${width}`),
        fullPage: true,
      });
      if (width === 320) {
        await page.route("**/api/health", (route) =>
          route.fulfill({ status: 503, json: { detail: "模拟服务不可用" } }),
        );
        await page.reload();
        await expect(page.getByRole("status")).toContainText(
          "服务不可用，请检查后端",
        );
        await noPageOverflow(page);
      }
      expect(errors).toEqual([]);
    });
  }
});

for (const width of [820, 1440]) {
  test(`${width}px: preserve table layout and avoid unnecessary scroll hints`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const created = await request.post("/api/projects", {
      data: { name: longName, description: "平板与桌面布局回归" },
    });
    expect(created.ok()).toBeTruthy();
    const project = await created.json();
    await page.goto(`/projects/${project.id}`);
    await page.getByLabel("选择上传文件").setInputFiles(fixture("sales.csv"));
    await expect(
      page.getByRole("table", { name: "数据预览", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "返回数据源", exact: true }),
    ).toBeHidden();
    await expect(
      page.getByRole("columnheader", { name: "文件名称", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("可左右滑动查看其余列", { exact: true }),
    ).toBeHidden();
    await noPageOverflow(page);
    await page.screenshot({
      path: screenshot(`workspace-${width}`),
      fullPage: true,
    });
  });
}
