import { expect, test, type APIRequestContext } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const output = resolve(process.env.STEP4_LIVE_DIR!);
const data = fileURLToPath(new URL("../data/business/", import.meta.url));
const manifest = () =>
  JSON.parse(readFileSync(resolve(output, "manifest.json"), "utf8"));

test.afterAll(() => {
  if (
    !manifest().cases.every((kind: string) =>
      existsSync(resolve(output, `${kind}.json`)),
    )
  )
    return;
  // 服务关闭前只读核对手算、原文件切片与工具结果；不启动第二个应用实例。
  const root = fileURLToPath(new URL("../..", import.meta.url));
  const python = resolve(
    root,
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  const summary = execFileSync(
    python,
    ["test/evaluation/step4_live.py", "--verify"],
    { cwd: root, encoding: "utf8", windowsHide: true },
  );
  console.log(summary.trim());
});

async function upload(
  request: APIRequestContext,
  path: string,
  filename: string,
  indexed = false,
) {
  const response = await request.post(path + "/sources", {
    multipart: {
      file: {
        name: filename,
        mimeType: filename.endsWith("pdf") ? "application/pdf" : "text/csv",
        buffer: readFileSync(resolve(data, filename)),
      },
    },
  });
  expect(response.status()).toBe(201);
  const source = await response.json();
  expect(source.status).toBe("ready");
  if (filename === "orders.csv") {
    const fields = Object.fromEntries(
      [
        "order_id",
        "paid_at",
        "paid_amount",
        "refund_amount",
        "region",
        "product",
        "status",
      ].map((field) => [field, field]),
    );
    const mapping = await request.put(`${path}/sources/${source.id}/mapping`, {
      data: { fields },
    });
    expect(mapping.ok()).toBe(true);
    expect((await mapping.json()).valid).toBe(true);
  }
  if (indexed) {
    const version = await request.post(`${path}/sources/${source.id}/indexes`, {
      data: filename.endsWith("csv")
        ? { text_field: "text", record_id_field: "feedback_id" }
        : {},
    });
    expect(version.status()).toBe(202);
    const id = (await version.json()).id;
    await expect
      .poll(
        async () => {
          const versions = await (await request.get(path + "/indexes")).json();
          return versions.find((value: { id: string }) => value.id === id)
            ?.status;
        },
        { timeout: 180_000, intervals: [500, 1000] },
      )
      .toBe("ready");
  }
  return source.id;
}

const kinds = ["normal", "missing", "injection"] as const;
const selectedCase = process.env.STEP4_LIVE_CASE;
if (selectedCase && !kinds.some((kind) => kind === selectedCase))
  throw new Error("Unrecognized explicit live evaluation case.");
for (const kind of kinds.filter(
  (kind) => !selectedCase || selectedCase === kind,
)) {
  test(`real webpage submission, stream, completion and history: ${kind}`, async ({
    page,
    request,
  }) => {
    const configuration = manifest();
    const projectResponse = await request.post("/api/projects", {
      data: { name: `步骤四真实网页·${kind}(test)` },
    });
    expect(projectResponse.status()).toBe(201);
    const project = await projectResponse.json();
    const path = `/api/projects/${project.id}`;
    const sourceIds = [await upload(request, path, "orders.csv")];
    if (kind === "normal") {
      sourceIds.push(await upload(request, path, "feedback.csv", true));
      sourceIds.push(await upload(request, path, "quarterly_report.pdf", true));
    } else if (kind === "injection")
      sourceIds.push(
        await upload(request, path, "feedback_injection.csv", true),
      );
    const config = await (
      await request.get(path + "/agent/configuration")
    ).json();
    expect(config.enabled && config.configured).toBe(true);
    const errors: string[] = [];
    const posts: {
      request_id: string;
      question: string;
      source_ids: string[];
    }[] = [];
    let streams = 0;
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (value) => {
      if (value.method() === "POST" && value.url().endsWith("/agent/runs"))
        posts.push(value.postDataJSON());
    });
    page.on("response", (response) => {
      if (
        response.url().includes("/stream?") &&
        response.status() === 200 &&
        response.headers()["content-type"]?.includes("text/event-stream")
      )
        streams++;
    });
    await page.goto(`/projects/${project.id}/agent`);
    await page
      .getByRole("textbox", { name: "分析问题" })
      .fill(configuration.questions[kind]);
    await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(
      sourceIds.length,
    );
    await page.getByRole("button", { name: "开始分析", exact: true }).click();
    await expect(page).toHaveURL(/\/agent\/runs\/[a-f0-9-]+$/);
    const id = new URL(page.url()).pathname.split("/").at(-1)!;
    const runPath = path + "/agent/runs/" + id;
    let run: any;
    let terminal = false;
    try {
      await expect(page.locator(".agent-run-status")).toContainText(
        "实时连接",
        { timeout: 8000 },
      );
      await expect(page.locator(".agent-progress")).toContainText("请求模型");
      if (kind === "normal") {
        // 刷新活动任务只恢复查询；不新建任务、不重发计费请求。
        await page.reload();
        await expect(page.locator(".agent-run-status")).toContainText(
          "实时连接",
          { timeout: 8000 },
        );
      }
      await expect
        .poll(
          async () => {
            run = await (await request.get(runPath)).json();
            return run.status;
          },
          { timeout: 310_000, intervals: [1000, 2000] },
        )
        .not.toMatch(/^(queued|running)$/);
      terminal = true;
      const events = await (await request.get(runPath + "/events")).json();
      writeFileSync(
        resolve(output, `${kind}.json`),
        JSON.stringify(
          {
            case: kind,
            project_id: project.id,
            run,
            events,
            streams,
            browser_submissions: posts.length,
          },
          null,
          2,
        ),
      );
      expect(run.status, run.error ?? "task should succeed").toBe("succeeded");
      await expect(page.locator(".agent-run-status")).toContainText("已完成", {
        timeout: 10_000,
      });
      await page.getByRole("link", { name: "分析报告", exact: true }).click();
      await expect(
        page.getByRole("heading", { name: run.report.title, exact: true }),
      ).toBeVisible();
      expect(streams).toBeGreaterThanOrEqual(kind === "normal" ? 2 : 1);
      expect(posts).toHaveLength(1);
      expect(posts[0].source_ids.sort()).toEqual(sourceIds.sort());
      const repeated = await request.post(path + "/agent/runs", {
        data: posts[0],
      });
      expect(repeated.status()).toBe(202);
      expect((await repeated.json()).id).toBe(id);
      expect((await repeated.json()).usage).toEqual(run.usage);
      if (kind === "normal") {
        await expect(page.locator(".agent-report")).toContainText("76000.00");
        await expect(page.locator(".agent-report")).toContainText("-24.00%");
        expect(run.report.chart_specs.length).toBeGreaterThan(0);
        await expect(page.locator(".chart-canvas svg").first()).toBeVisible();
        for (const width of [1440, 320]) {
          await page.setViewportSize({ width, height: 1000 });
          const csv = run.report.sources.find(
            (source: any) => source.kind === "csv" && source.records?.[0] === 5,
          );
          expect(csv).toBeTruthy();
          const csvRow = page
            .locator(".agent-evidence li")
            .filter({ hasText: `· ${csv.id}` })
            .first();
          const csvButton = csvRow.getByRole("button", { name: "查看原文" });
          await csvButton.click();
          await expect(csvRow).toHaveClass(/selected/);
          await expect(page.locator("tr.citation-row")).toContainText("0005");
          await page
            .locator(".agent-preview > .section-heading")
            .getByRole("button", { name: "返回分析报告", exact: true })
            .filter({ visible: true })
            .click();
          await expect(csvButton).toBeFocused();
          const doc = run.report.sources.find(
            (source: any) => source.kind === "document" && source.page,
          );
          expect(doc).toBeTruthy();
          const docRow = page
            .locator(".agent-evidence li")
            .filter({ hasText: `· ${doc.id}` })
            .first();
          await docRow.getByRole("button", { name: "查看原文" }).click();
          await expect(docRow).toHaveClass(/selected/);
          await expect(page.locator("mark.citation-highlight")).toHaveText(
            doc.text,
          );
          await page.screenshot({
            path: resolve(output, `original-${width}.png`),
            fullPage: true,
          });
          await page
            .locator(".agent-preview > .section-heading")
            .getByRole("button", { name: "返回分析报告", exact: true })
            .filter({ visible: true })
            .click();
          expect(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
          ).toBe(true);
        }
      } else if (kind === "missing") {
        await expect(page.locator(".agent-findings")).toContainText("广告");
        await expect(
          page.locator(".agent-finding-label").filter({ hasText: "无法确认" }),
        ).not.toHaveCount(0);
      } else expect(run.report.title).not.toContain("INJECTION_WON");
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
      await expect(page.locator(".history-list")).toContainText(
        run.report.title,
      );
      await page
        .getByRole("textbox", { name: "关键词", exact: true })
        .fill(run.report.title);
      await page.getByRole("button", { name: "查询历史", exact: true }).click();
      await expect(page.locator(".history-list li")).toHaveCount(1);
      await page.locator(".history-list li a").click();
      await expect(
        page.getByRole("heading", { name: run.report.title, exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "用此问题新建分析", exact: true })
        .click();
      await expect(page.getByRole("textbox", { name: "分析问题" })).toHaveValue(
        configuration.questions[kind],
      );
      expect(posts).toHaveLength(1);
      expect(errors).toEqual([]);
      expect(
        await (await request.get(path + "/agent/runs")).json(),
      ).toHaveLength(1);
    } finally {
      if (!terminal) {
        await request.post(runPath + "/cancel");
        run = await (await request.get(runPath)).json();
        writeFileSync(
          resolve(output, `${kind}.json`),
          JSON.stringify(
            {
              case: kind,
              project_id: project.id,
              run,
              streams,
              browser_submissions: posts.length,
              notice: "网页未完成验证，已请求取消；未重发任务。",
            },
            null,
            2,
          ),
        );
      }
    }
  });
}
