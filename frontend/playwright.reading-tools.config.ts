import base from "./playwright.unified.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  testMatch: "reading-tools.spec.ts",
  testIgnore: [],
  timeout: 90000,
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_STRUCTURED=1 IPAPER_BROWSER_READING_TOOLS=1 .venv/bin/python -m tests.unified_server",
    reuseExistingServer: false,
  },
});
