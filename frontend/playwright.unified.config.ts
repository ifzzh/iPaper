import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  testDir: "./unified-tests",
  testIgnore: ["structured.spec.ts", "understanding.spec.ts", "reading-tools.spec.ts", "metadata.spec.ts"],
  use: {
    ...base.use,
    launchOptions: { executablePath: "/opt/microsoft/msedge/msedge" },
  },
  webServer: {
    ...(base.webServer as object),
    command:
      "cd .. && exec env PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m tests.unified_server",
    url: "http://127.0.0.2:7191/",
    timeout: 60000,
  },
});
