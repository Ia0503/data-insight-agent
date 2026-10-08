import { expect, test } from "@playwright/test";

test("real built-in import preserves project, five files, mapping and active indexes on retry", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  let modelSubmissions = 0;
  page.on("request", (value) => {
    if (value.method() === "POST" && /\/agent\/runs$/.test(value.url()))
      modelSubmissions++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "导入业务示例", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "导入业务示例", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  const catalog = async () => {
    const response = await request.get(
      "http://127.0.0.1:8001/api/examples/business",
    );
    expect(response.ok()).toBe(true);
    return response.json();
  };
  await expect
    .poll(
      async () =>
        (await catalog()).files.filter(
          (v: { index_status: string }) => v.index_status === "ready",
        ).length,
      { timeout: 60000 },
    )
    .toBe(3);
  const first = await catalog();
  expect(first.files).toHaveLength(5);
  expect(
    first.files.every((v: { status: string }) => v.status === "ready"),
  ).toBe(true);
  expect(first.project.name).toMatch(/\(test\)$/);
  const order = first.files.find(
    (v: { filename: string }) => v.filename === "orders.csv",
  );
  const mapping = await (
    await request.get(
      `http://127.0.0.1:8001/api/projects/${first.project.id}/sources/${order.source_id}/mapping`,
    )
  ).json();
  await page.getByRole("button", { name: "导入业务示例", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "导入业务示例", exact: true }),
  ).toBeEnabled();
  const second = await catalog();
  expect(second.project.id).toBe(first.project.id);
  expect(second.files.map((v: { source_id: string }) => v.source_id)).toEqual(
    first.files.map((v: { source_id: string }) => v.source_id),
  );
  const after = await (
    await request.get(
      `http://127.0.0.1:8001/api/projects/${first.project.id}/sources/${order.source_id}/mapping`,
    )
  ).json();
  expect(after.version).toBe(mapping.version);
  const indexes = await (
    await request.get(
      `http://127.0.0.1:8001/api/projects/${first.project.id}/indexes`,
    )
  ).json();
  expect(indexes).toHaveLength(3);
  expect(indexes.every((v: { active: boolean }) => v.active)).toBe(true);
  expect(modelSubmissions).toBe(0);
  await page.getByRole("link", { name: "查看示例项目" }).click();
  await expect(
    page.getByRole("heading", { name: first.project.name, exact: true }),
  ).toBeVisible();
});
