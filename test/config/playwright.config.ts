import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const python = fileURLToPath(
  new URL(
    process.platform === "win32"
      ? "../../.venv/Scripts/python.exe"
      : "../../.venv/bin/python",
    import.meta.url,
  ),
);
const testServer = fileURLToPath(new URL("../serve_test.py", import.meta.url));

export default defineConfig({
  testDir: "../e2e",
  outputDir: "../results/browser",
  workers: 1,
  reporter: [["list"], ["json", { outputFile: fileURLToPath(new URL("../results/playwright.json", import.meta.url)) }]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    channel: process.env.E2E_CHANNEL || "msedge",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `"${python}" "${testServer}"`,
      cwd: root,
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: false,
    },
    {
      command: "node test/serve_frontend.mjs",
      cwd: root,
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
    },
  ],
});
