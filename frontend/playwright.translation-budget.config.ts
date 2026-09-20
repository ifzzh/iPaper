import base from "./playwright.unified.config";
import { defineConfig } from "@playwright/test";

// Focused suite for the structured-translation budget form. Same synthetic
// structured fixture (fake cloud and fake model) as the structured suite.
export default defineConfig({
  ...base,
  testMatch: "translation-budget.spec.ts",
  testIgnore: [],
  timeout: 90000,
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_STRUCTURED=1 IPAPER_BROWSER_STOPPED_JOB=1 .venv/bin/python -m tests.unified_server",
    reuseExistingServer: false,
  },
});
