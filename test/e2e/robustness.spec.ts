import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

async function project(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", { data: { name } });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function switchProject(page: Page, id: string) {
  // Exercise reuse of the same detail component, rather than a full page reload.
  await page.evaluate(async (target) => {
    const { default: router } = await import("/src/router.ts");
    await router.push(`/projects/${target}`);
  }, id);
}
async function renderSettled(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

for (const operation of ["save", "retry"]) {
  test(`late ${operation} response cannot replace a different project`, async ({
    page,
    request,
  }) => {
    const first = await project(request, "旧项目(test)");
    const second = await project(request, "新项目(test)");
    let source: any;
    if (operation === "retry") {
      const response = await request.post(`/api/projects/${first.id}/sources`, {
        multipart: {
          file: {
            name: "bad.csv",
            mimeType: "text/csv",
            buffer: Buffer.from("a,a\n1,2\n"),
          },
        },
      });
      source = await response.json();
    }
    let release!: () => void;
    let received!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const entered = new Promise<void>((resolve) => {
      received = resolve;
    });
    const path =
      operation === "save"
        ? `/api/projects/${first.id}`
        : `/api/projects/${first.id}/sources/${source.id}/retry`;
    await page.route(`**${path}`, async (route) => {
      if (route.request().method() === "GET") return route.continue();
      received();
      await gate;
      await route.fulfill({
        json:
          operation === "save"
            ? { ...first, name: "旧项目已更新(test)" }
            : { ...source, status: "ready", error: null },
      });
    });
    await page.goto(`/projects/${first.id}`);
    if (operation === "save") {
      await page.getByRole("button", { name: "编辑项目" }).click();
      await page
        .getByRole("textbox", { name: "项目名称", exact: true })
        .fill("旧项目已更新(test)");
      await page.getByRole("button", { name: "保存修改" }).click();
    } else
      await page.getByRole("button", { name: "重试", exact: true }).click();
    await entered;
    try {
      await switchProject(page, second.id);
      await expect(
        page.getByRole("heading", { name: "新项目(test)", exact: true }),
      ).toBeVisible();
      const completed = page.waitForResponse(
        (response) =>
          response.url().endsWith(path) &&
          response.request().method() !== "GET",
      );
      release();
      await (await completed).finished();
      await renderSettled(page);
      await expect(
        page.getByRole("heading", { name: "新项目(test)", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("heading", { name: "旧项目已更新(test)" }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("region", { name: "当前文件预览" }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "编辑项目" }),
      ).toBeEnabled();
      await expect(
        page.getByRole("textbox", { name: "项目名称", exact: true }),
      ).toHaveCount(0);
    } finally {
      release();
    }
  });
}

test("cancel restores saved form values and failed preview can retry its current page", async ({
  page,
  request,
}) => {
  const value = await project(request, "分页与取消验证(test)");
  await page.goto(`/projects/${value.id}`);
  await page.getByRole("button", { name: "编辑项目" }).click();
  await page
    .getByRole("textbox", { name: "项目名称", exact: true })
    .fill("未保存修改");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "编辑项目" }).click();
  await expect(
    page.getByRole("textbox", { name: "项目名称", exact: true }),
  ).toHaveValue("分页与取消验证(test)");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page
    .getByLabel("选择上传文件")
    .setInputFiles({
      name: "pages.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "value\n" +
          Array.from({ length: 45 }, (_, index) => `${index}\n`).join(""),
      ),
    });
  await expect(page.getByText("第 1 / 3 页", { exact: true })).toBeVisible();
  let fail = true;
  await page.route("**/preview?page=2&*", (route) => {
    if (!fail) return route.continue();
    fail = false;
    return route.fulfill({ status: 503, json: { detail: "预览暂时不可用" } });
  });
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("预览暂时不可用");
  await expect(page.getByText("第 2 / 3 页", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重试预览", exact: true }).click();
  await expect(
    page
      .getByRole("table", { name: "数据预览", exact: true })
      .getByRole("cell", { name: "20", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a hung write times out without duplicate submission and invalid JSON is rejected", async ({
  page,
}) => {
  await page.clock.install();
  await page.goto("/");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page
    .getByRole("textbox", { name: "项目名称", exact: true })
    .fill("超时验证");
  let release!: () => void;
  let entered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const received = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let calls = 0;
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    calls++;
    entered();
    await gate;
    await route.fulfill({
      status: 200,
      body: "not-json",
      contentType: "text/plain",
    });
  });
  await page.getByRole("button", { name: "保存项目" }).click();
  await received;
  await page.locator("form").evaluate((form: HTMLFormElement) => {
    form.requestSubmit();
    form.requestSubmit();
  });
  await page.clock.runFor(15_001);
  await expect(page.getByRole("alert")).toContainText("操作可能已在服务端完成");
  expect(calls).toBe(1);
  release();
  await page.getByRole("button", { name: "保存项目" }).click();
  await expect(page.getByRole("alert")).toContainText("无效响应");
  await expect(page).toHaveURL("http://127.0.0.1:5174/");
});

test("connection indicator reflects service failure after its initial check", async ({
  page,
}) => {
  await page.clock.install();
  await page.goto("/");
  await expect(page.getByRole("status")).toContainText("服务已连接");
  await page.route("**/api/health", (route) =>
    route.fulfill({ status: 503, json: { detail: "暂时不可用" } }),
  );
  await page.clock.runFor(30_001);
  await expect(page.getByRole("status")).toContainText("服务不可用");
});
