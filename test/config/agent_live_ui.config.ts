import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export default defineConfig(base, {
  testDir: "../evaluation",
  testMatch: "agent_live_ui.spec.ts",
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile: process.env.AGENT_LIVE_DIR
          ? resolve(process.env.AGENT_LIVE_DIR, "browser-saved.json")
          : fileURLToPath(
              new URL(
                process.env.AGENT_LIVE_RESULT === "results-normal.json"
                  ? "../results/agent-live/browser-v2.json"
                  : "../results/agent-live/browser.json",
                import.meta.url,
              ),
            ),
      },
    ],
  ],
});
