import base from "./playwright.config";
import { defineConfig } from "@playwright/test";

// Focused suite for the topic sidebar loading behaviour. It runs on its own
// server so the assertions do not depend on the account/view state left behind
// by the long shared unified sequence.
export default defineConfig({
  ...base,
  testDir: "./unified-tests",
  testMatch: ["topics-loading.spec.ts"],
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
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m tests.unified_server",
    url: "http://127.0.0.2:7191/",
    timeout: 60000,
  },
});
