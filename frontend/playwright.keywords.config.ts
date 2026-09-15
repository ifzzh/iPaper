import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./unified-tests",
  testMatch: "keywords.spec.ts",
  workers: 1,
  timeout: 90000,
  use: {
    baseURL: "http://127.0.0.2:7191",
    browserName: "chromium",
    launchOptions: {
      executablePath: "/opt/microsoft/msedge/msedge",
      env: { ...process.env, XDG_CONFIG_HOME: "/tmp/ipaper-edge-config" },
    },
  },
  webServer: {
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_KEYWORDS=1 .venv/bin/python -m tests.unified_server",
    url: "http://127.0.0.2:7191/",
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
    timeout: 60000,
  },
});
