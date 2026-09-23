import base from "./playwright.unified.config";
import { defineConfig } from "@playwright/test";

// Runs the deployed-image acceptance spec against the current source build on the
// isolated stack (fake suppliers, synthetic fixtures) before shipping it.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: "production-notes-v132.spec.ts",
  testIgnore: [],
  globalSetup: "./unified-tests/notes.setup.ts",
  fullyParallel: false,
  workers: 1,
  use: { ...(base.use as object), storageState: "/tmp/ipaper-notes-state.json", baseURL: "http://127.0.0.2:7191" },
  timeout: 300000,
});
