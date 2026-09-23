import base from "./playwright.notes.config";
import { defineConfig } from "@playwright/test";

// Run against a fresh synthetic server so earlier specs cannot spend this
// owner's rate-limit window or leave unrelated note sources/preferences.
export default defineConfig({
  ...base,
  testMatch: "notes-reliability.spec.ts",
});
