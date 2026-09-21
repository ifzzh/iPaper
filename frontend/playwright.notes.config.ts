import base from "./playwright.unified.config";
import { defineConfig } from "@playwright/test";

// Reading-notes suite: the structured fixture provides real synthetic PDFs and a
// parse result so highlights/annotations bind to real document revisions.
export default defineConfig({
  ...base,
  testMatch: "notes.spec.ts",
  testIgnore: [],
  timeout: 120000,
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_STRUCTURED=1 IPAPER_BROWSER_STOPPED_JOB=1 .venv/bin/python -m tests.unified_server",
    reuseExistingServer: false,
  },
});
