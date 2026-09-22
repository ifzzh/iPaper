import { request } from "@playwright/test";

// One login for the whole notes suite: the isolated server rate-limits logins,
// and every test only needs the resulting session.
export default async function globalSetup() {
  const context = await request.newContext({ baseURL: "http://127.0.0.2:7191" });
  const response = await context.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
  if (!response.ok()) throw new Error(`login failed: ${response.status()}`);
  await context.storageState({ path: "/tmp/ipaper-notes-state.json" });
  await context.dispose();
}
