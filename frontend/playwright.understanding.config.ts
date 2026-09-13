import base from "./playwright.structured.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...base,
  testMatch: "understanding.spec.ts",
  timeout: 120000,
});
