import { test, expect } from "@playwright/test";

// Regression: the topic sidebar polled /api/topics every 5s and rendered the
// blocking "正在加载…" spinner on every poll, so a slow or aborted request left it
// spinning while the tree was already on screen. It must only show that spinner
// before its first successful load, clear it as soon as data arrives, and not
// pile requests up.
//
// Uses its own account so reading the library cannot disturb the exact paper
// counts other specs assert for their users.
test("topic sidebar clears its spinner and polls slowly", async ({ page }) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });

  // Slow down every topic read so a stuck-loading state is easy to observe.
  let topicRequests = 0;
  const delayMs = 2500;
  await page.route("**/api/topics", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    topicRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.continue();
  });

  // Clear reader tabs this account may carry from other specs so the library is
  // the view that renders.
  const csrf = (await page.context().cookies()).find(
    (cookie) => cookie.name === "paperpilot_csrf",
  )?.value;
  await page.request.put("/api/workspace/state", {
    headers: { "X-CSRF-Token": csrf || "" },
    data: { tabs: [], activePaper: null, theme: "light" },
  });

  // Register the waiter before navigating: with a delayed route the response can
  // land while `goto` is still resolving.
  const firstTopics = page.waitForResponse(
    (response) =>
      response.url().includes("/api/topics") &&
      response.request().method() === "GET",
    { timeout: 20000 },
  );
  await page.goto("/");
  await firstTopics;

  // Once the first response is in, the spinner must disappear and stay gone.
  await expect(page.locator(".sidebar-topics .loading")).toHaveCount(0, {
    timeout: 20000,
  });
  const samples: boolean[] = [];
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    samples.push(await page.locator(".sidebar-topics .loading").isVisible());
    await page.waitForTimeout(250);
  }
  expect(samples.filter(Boolean)).toHaveLength(0);

  // A 5s poll would have produced at least two more requests in this window.
  expect(topicRequests).toBeLessThanOrEqual(2);
});
