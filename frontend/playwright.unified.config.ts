import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  testDir: "./unified-tests",
  testIgnore: [
    "production-notes.spec.ts",
    "production-notes-closeout.spec.ts",
    "production-notes-theme.spec.ts",
    "production-notes-v132.spec.ts",
    "daily-states.spec.ts",
    "notes.spec.ts",
    "notes-reliability.spec.ts",
    "translation-budget.spec.ts",
    "topics-loading.spec.ts",
    "list-loading.spec.ts",
    "structured.spec.ts",
    "understanding.spec.ts",
    "reading-tools.spec.ts",
    "metadata.spec.ts",
    "keywords.spec.ts",
  ],
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
