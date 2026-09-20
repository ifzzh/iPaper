import base from "./playwright.config";
import { defineConfig } from "@playwright/test";

// Focused Daily suite: its own server seeds extra synthetic asset states, so the
// shared unified sequence keeps its minimal Day view.
export default defineConfig({
  ...base,
  testDir: "./unified-tests",
  testMatch: ["daily-states.spec.ts"],
  testIgnore: [],
  use: {
    ...base.use,
    launchOptions: {
      ...(base.use as { launchOptions?: object }).launchOptions,
      executablePath: "/opt/microsoft/msedge/msedge",
    },
  },
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_DAILY_STATES=1 .venv/bin/python -m tests.unified_server",
    url: "http://127.0.0.2:7191/",
    timeout: 60000,
  },
});
