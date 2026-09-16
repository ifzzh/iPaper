import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  testDir: "./compat-tests",
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m tests.workbench_server",
  },
  use: {
    ...base.use,
    launchOptions: {
      ...(base.use as { launchOptions?: object }).launchOptions,
      executablePath: "/opt/microsoft/msedge/msedge",
    },
  },
});
