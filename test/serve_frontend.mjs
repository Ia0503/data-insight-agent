import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

// Resolve production tooling from frontend; keep test-only server settings in test/.
const require = createRequire(
  new URL("../frontend/package.json", import.meta.url),
);
const { createServer } = await import(
  pathToFileURL(require.resolve("vite")).href
);
const { default: vue } = await import(
  pathToFileURL(require.resolve("@vitejs/plugin-vue")).href
);
const live = process.argv.includes("--live-step4");
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL("../frontend", import.meta.url)),
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: live ? 5175 : 5174,
    strictPort: true,
    proxy: { "/api": `http://127.0.0.1:${live ? 8002 : 8001}` },
  },
});
await server.listen();
server.printUrls();
