import { expect, test } from "@playwright/test";
import { json, setup } from "./agent.fixture";

for (const width of [1440, 320]) {
  test(`analysis actions and full width input stay usable at ${width}px`, async ({
    page,
  }) => {
    const data = await setup(page);
    await page.route(
      `**/api/projects/${data.project.id}/analysis/model`,
      (route) =>
        route.fulfill(
          json({ downloaded: false, profile: { model_id: "test" } }),
        ),
    );
    await page.route(
      `**/api/projects/${data.project.id}/sources/*/mapping`,
      (route) => route.fulfill(json({ fields: {} })),
    );
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/projects/${data.project.id}/analysis`);
    const heading = page.locator(".page-heading");
    const details = heading.getByRole("link", {
      name: "项目详情",
      exact: true,
    });
    const agent = heading.getByRole("link", { name: "智能分析", exact: true });
    await expect(details).toBeVisible();
    await expect(agent).toHaveClass(/primary/);
    const first = (await details.boundingBox())!;
    const second = (await agent.boundingBox())!;
    expect(Math.abs(first.y - second.y)).toBeLessThan(1);
    if (width === 320) expect(second.height).toBeGreaterThanOrEqual(44);
    await agent.click();
    await expect(page.getByRole("textbox", { name: "分析问题" })).toBeVisible();
    const input = (await page.locator(".agent-create").boundingBox())!;
    await page.getByRole("link", { name: "历史分析", exact: true }).click();
    const historyHeading = page.locator(".page-heading");
    await expect(historyHeading.getByRole("link")).toHaveText([
      "项目详情",
      "分析与检索",
    ]);
    await expect(
      page.getByRole("link", { name: "新建分析", exact: true }),
    ).toHaveCount(1);
    const history = (await page.locator(".agent-panel").boundingBox())!;
    expect(Math.abs(input.x - history.x)).toBeLessThan(1);
    expect(Math.abs(input.width - history.width)).toBeLessThan(1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("quick health results never display the intermediate connecting badge, including reload", async ({
  page,
}) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as any).connectionFrames = seen;
    function observe() {
      const badge = document.querySelector(".connection");
      if (badge && getComputedStyle(badge).visibility !== "hidden")
        seen.push(badge.textContent!.trim());
      requestAnimationFrame(observe);
    }
    requestAnimationFrame(observe);
  });
  for (let open = 0; open < 2; open++) {
    if (open === 0) await page.goto("/");
    else await page.reload();
    await expect(page.locator(".connection")).toContainText("服务已连接");
    const frames: string[] = await page.evaluate(
      () => (window as any).connectionFrames,
    );
    expect(frames.some((value) => /正在连接|连接中/.test(value))).toBe(false);
  }
});

test("slow initial health check waits before showing pending, then reports failure and recovery", async ({
  page,
}) => {
  await page.clock.install();
  await page.setViewportSize({ width: 320, height: 1000 });
  let release!: () => void;
  let entered!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const received = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let healthy = false;
  let calls = 0;
  await page.route("**/api/health", async (route) => {
    if (++calls === 1) {
      entered();
      await held;
    }
    await route.fulfill(
      healthy
        ? { json: { status: "ok" } }
        : { status: 503, json: { detail: "暂时不可用" } },
    );
  });
  try {
    await page.goto("/");
    await received;
    const badge = page.locator(".connection");
    await expect(badge).toBeHidden();
    await page.clock.runFor(401);
    await expect(badge).toBeVisible();
    await expect(badge).toContainText("正在连接");
    release();
    await expect(badge).toContainText("服务不可用");
    healthy = true;
    await page.clock.runFor(30_001);
    await expect(badge).toContainText("服务已连接");
    expect(calls).toBe(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  } finally {
    release();
  }
});
