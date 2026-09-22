import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "production-notes-theme.spec.ts",
  timeout: 180000,
  retries: 0,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191",
    launchOptions: {
      executablePath: "/opt/microsoft/msedge/msedge",
      env: { ...process.env, XDG_CONFIG_HOME: "/tmp/ipaper-edge-config-theme" },
    },
  },
});
