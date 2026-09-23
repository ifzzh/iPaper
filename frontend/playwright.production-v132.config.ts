import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "production-notes-v132.spec.ts",
  timeout: 300000,
  retries: 0,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191",
    trace: "retain-on-failure",
    launchOptions: {
      executablePath: "/opt/microsoft/msedge/msedge",
      env: { ...process.env, XDG_CONFIG_HOME: "/tmp/ipaper-edge-v132" },
    },
  },
});
