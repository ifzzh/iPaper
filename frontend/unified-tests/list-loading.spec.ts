import { test, expect } from "@playwright/test";

// Regression: the library list refreshed /api/library/index every 5s and showed
// the blocking "正在加载…" indicator between the filters and the rows on every
// poll, so the list looked like it was permanently reloading. Background
// refreshes must stay silent, and the poll must be slow and non-overlapping.
test("library list does not flash its spinner on background refresh", async ({
  page,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });

  let listRequests = 0;
  const delayMs = 2000;
  await page.route("**/api/library/index*", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    listRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.continue();
  });

  // Drop reader tabs this account may carry from other specs.
  const csrf = (await page.context().cookies()).find(
    (cookie) => cookie.name === "paperpilot_csrf",
  )?.value;
  await page.request.put("/api/workspace/state", {
    headers: { "X-CSRF-Token": csrf || "" },
    data: { tabs: [], activePaper: null, theme: "light" },
  });

  const firstList = page.waitForResponse(
    (response) =>
      response.url().includes("/api/library/index") &&
      response.request().method() === "GET",
    { timeout: 20000 },
  );
  await page.goto("/?view=library");
  await firstList;

  await expect(page.locator(".paper-row").first()).toBeVisible({ timeout: 20000 });
  await expect(page.locator(".library-list-panel .loading")).toHaveCount(0, {
    timeout: 20000,
  });

  const afterFirstLoad = listRequests;
  const samples: boolean[] = [];
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    samples.push(await page.locator(".library-list-panel .loading").isVisible());
    await page.waitForTimeout(250);
  }
  expect(samples.filter(Boolean)).toHaveLength(0);

  // A 5s poll would have added at least one more request in this window.
  expect(listRequests).toBeLessThanOrEqual(afterFirstLoad + 2);
});