import { defineConfig } from "@playwright/test";

// Manual acceptance against the deployed service: no webServer, the caller
// injects an owner-scoped maintenance session and a real paper id.
export default defineConfig({
  testDir: ".",
  testMatch: "production-notes.spec.ts",
  timeout: 180000,
  retries: 0,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191",
    trace: "retain-on-failure",
    launchOptions: {
      executablePath: "/opt/microsoft/msedge/msedge",
      // Edge writes crashpad state under XDG_CONFIG_HOME; keep it in /tmp.
      env: { ...process.env, XDG_CONFIG_HOME: "/tmp/ipaper-edge-config-production" },
    },
  },
});
