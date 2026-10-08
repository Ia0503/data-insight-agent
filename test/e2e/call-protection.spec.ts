import { expect, test, type Page } from "@playwright/test";
import { json, setup } from "./agent.fixture";

const password = "test-only-call-password";
async function protectedPage(page: Page, remaining = 1_000_000) {
  const state = await setup(page);
  await page.route("**/api/projects/*/agent/configuration", (route) =>
    route.fulfill(
      json({
        configured: true,
        enabled: true,
        provider: "custom",
        model: "模拟模型",
        notice: "测试配置",
        password_required: true,
        access_configured: true,
        quota: {
          limit: 1_000_000,
          used: 1_000_000 - remaining,
          reserved: 0,
          remaining,
          can_start: remaining > 6144,
          unknown_requests: 0,
          reset_at: "2026-10-07T01:00:00Z",
          timezone: "Asia/Shanghai",
        },
      }),
    ),
  );
  await state.open();
  await page.getByLabel("分析问题").fill("比较九月与八月净销售额。");
  return {
    ...state,
    setRemaining: (value: number) => {
      remaining = value;
    },
  };
}

test("每次新建分析需要密码，草稿不保存密码(test)", async ({ page }) => {
  const state = await protectedPage(page);
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("调用密码").fill(password);
  const request = page.waitForRequest(
    (value) => value.method() === "POST" && value.url().endsWith("/agent/runs"),
  );
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  const submitted = await request;
  expect(submitted.headers()["x-analysis-password"]).toBe(
    encodeURIComponent(password),
  );
  expect(submitted.postData()).not.toContain(password);
  await expect(page).toHaveURL(new RegExp(`/runs/${state.run.id}$`));
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...sessionStorage, ...localStorage }),
    ),
  ).not.toContain(password);
  await page.goto(`/projects/${state.project.id}/agent`);
  await expect(page.getByLabel("调用密码")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
});

for (const status of [401, 429]) {
  test(`拒绝 ${status} 后清空密码且不产生未知提交(test)`, async ({ page }) => {
    await protectedPage(page);
    await page.route("**/api/projects/*/agent/runs", (route) =>
      route.request().method() === "POST"
        ? route.fulfill(
            json({ detail: "调用已拒绝，请重新输入或等待额度重置。" }, status),
          )
        : route.fulfill(json([])),
    );
    await page.getByLabel("调用密码").fill(password);
    await page.getByRole("button", { name: "开始分析", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("调用已拒绝");
    await expect(page.getByLabel("调用密码")).toHaveValue("");
    await expect(page.getByRole("button", { name: "确认原提交" })).toHaveCount(
      0,
    );
  });
}

test("提交确认丢失时复用编号读取原任务(test)", async ({ page }) => {
  const state = await protectedPage(page);
  let attempts = 0;
  let original = "";
  await page.route("**/api/projects/*/agent/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fulfill(json([]));
    const body = route.request().postDataJSON();
    attempts++;
    if (attempts === 1) {
      original = body.request_id;
      state.run.request_id = original;
      return route.abort("failed");
    }
    expect(body.request_id).toBe(original);
    expect(route.request().headers()["x-analysis-password"]).toBeUndefined();
    return route.fulfill(json(state.run, 202));
  });
  await page.getByLabel("调用密码").fill(password);
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认原提交" })).toBeVisible();
  await expect(page.getByLabel("调用密码")).toHaveValue("");
  await page.getByRole("button", { name: "确认原提交" }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${state.run.id}$`));
  expect(attempts).toBe(2);
});

test("额度耗尽禁用新分析，320px 控件不溢出(test)", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 780 });
  await protectedPage(page, 0);
  await page.getByLabel("调用密码").fill(password);
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".quota-status")).toContainText("本小时可用 0");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(320);
});

test("跨小时额度恢复后自动刷新可用状态(test)", async ({ page }) => {
  await page.clock.install();
  const state = await protectedPage(page, 0);
  await page.getByLabel("调用密码").fill(password);
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
  state.setRemaining(1_000_000);
  await page.clock.fastForward(20_000);
  await expect(page.locator(".quota-status")).toContainText(
    "本小时可用 1,000,000",
  );
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeEnabled();
});
