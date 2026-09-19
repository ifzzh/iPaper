import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// The real auth limiter allows five logins per user per 15 minutes. A growing
// suite cannot share one temporary server indefinitely: later specs otherwise
// receive 429 before testing their UI. Each shard starts a fresh isolated
// server via playwright.config.ts; the application security policy is intact.
const cli = fileURLToPath(new URL("../node_modules/@playwright/test/cli.js", import.meta.url));
const requested = process.argv.slice(2);
const runs = requested.length
  ? [requested]
  : [["--shard=1/3"], ["--shard=2/3"], ["--shard=3/3"]];

for (const args of runs) {
  const result = spawnSync(process.execPath, [cli, "test", ...args], {
    stdio: "inherit",
    env: process.env,
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status || 1);
}
