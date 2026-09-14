import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./unified-tests",
  testIgnore: ["structured.spec.ts", "understanding.spec.ts", "reading-tools.spec.ts"],
  workers: 1,
  timeout: 30000,
  use: { baseURL: "http://127.0.0.2:7191", browserName: "chromium" },
  webServer: {
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m tests.unified_server",
    url: "http://127.0.0.2:7191/",
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
    timeout: 60000,
  },
});
