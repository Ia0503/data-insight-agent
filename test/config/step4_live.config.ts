import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve, relative, isAbsolute } from "node:path";

// 真实计费联调与默认回归隔离，必须人为开启；重试为零。
if (process.env.STEP4_LIVE_RUN !== "1" || !process.env.STEP4_LIVE_DIR)
  throw new Error(
    "Set STEP4_LIVE_RUN=1 and STEP4_LIVE_DIR after explicit authorization.",
  );
const root = fileURLToPath(new URL("../..", import.meta.url));
const output = resolve(process.env.STEP4_LIVE_DIR);
const resultPath = relative(resolve(root, "test/results"), output);
if (resultPath.startsWith("..") || isAbsolute(resultPath))
  throw new Error("Live artifacts must stay inside test/results.");
const budget = Number(process.env.STEP4_LIVE_MAX_CALLS ?? 32);
if (!Number.isInteger(budget) || budget < 1 || budget > 48)
  throw new Error("Invalid explicit model-request limit.");
const python = resolve(
  root,
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python",
);

// 单对象替换服务列表，避免多参数 defineConfig 合并后启动默认测试服务。
export default defineConfig({
  ...base,
  testDir: "../evaluation",
  testMatch: "step4_live_web.spec.ts",
  retries: 0,
  workers: 1,
  timeout: 420_000,
  outputDir: resolve(output, "browser"),
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(output, "browser.json") }],
  ],
  use: { ...base.use, baseURL: "http://127.0.0.1:5175", trace: "off" },
  webServer: [
    {
      command: `"${python}" test/evaluation/step4_live.py --run --max-calls ${budget}`,
      cwd: root,
      url: "http://127.0.0.1:8002/api/health",
      reuseExistingServer: false,
    },
    {
      command: "node test/serve_frontend.mjs --live-step4",
      cwd: root,
      url: "http://127.0.0.1:5175",
      reuseExistingServer: false,
    },
  ],
});
