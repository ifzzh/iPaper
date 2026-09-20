import { test, expect } from "@playwright/test";

// Daily asset states and card layout, on synthetic data (an isolated temporary
// database seeded by IPAPER_BROWSER_DAILY_STATES). No real model, MinerU, OCR,
// translation or vendor call is involved.
test("daily cards show honest asset states and recover without a page reload", async ({
  page,
}) => {
  const assetRetries: Array<{ url: string; body: string }> = [];
  const readMarks: string[] = [];
  const paperListRequests: number[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/retry") && request.method() === "POST") {
      assetRetries.push({ url, body: request.postData() || "" });
    }
    if (url.includes("/api/daily-arxiv/read/mark")) readMarks.push(url);
    if (url.includes("/api/daily-arxiv/papers/")) paperListRequests.push(Date.now());
  });

  await page.request.post("/api/auth/login", {
    data: { username: "reader_one", password: "workbench-test-pass" },
  });
  await page.goto("/?view=daily");
  await expect(page.locator(".daily-card").first()).toBeVisible({ timeout: 20000 });

  // Every card uses the same cover frame, so an image appearing never pushes the
  // text around and the states cannot drift apart.
  const frames = await page.locator(".daily-card .daily-cover-frame, .daily-card .daily-cover-placeholder").all();
  expect(frames.length).toBeGreaterThanOrEqual(4);
  const heights = new Set<number>();
  for (const frame of frames) {
    const box = await frame.boundingBox();
    if (box) heights.add(Math.round(box.height));
  }
  expect(heights.size).toBe(1);

  // A readable paper with no preview at all must offer the cover-only repair
  // (this is the state the reported screenshot was stuck in).
  const noCoverCard = page.locator(".daily-card", { hasText: "Daily 合成验收" });
  await expect(noCoverCard).toContainText("封面待生成");
  await expect(
    noCoverCard.getByRole("button", { name: /重新生成封面/ }),
  ).toBeVisible();

  // A readable paper whose preview failed offers the cover-only retry.
  const coverCard = page.locator(".daily-card", {
    hasText: "PDF 可读但缺封面",
  });
  await expect(coverCard).toContainText("封面生成失败");
  const coverRetry = coverCard.getByRole("button", { name: /重新生成封面/ });
  await expect(coverRetry).toBeVisible();
  await coverRetry.click();
  await expect
    .poll(() => assetRetries.length, { timeout: 15000 })
    .toBeGreaterThan(0);
  expect(JSON.parse(assetRetries.at(-1)!.body)).toMatchObject({
    stage: "thumbnail",
  });

  // A failed PDF offers the PDF retry, and the request says so.
  const failedCard = page.locator(".daily-card", { hasText: "PDF 获取失败" });
  await expect(failedCard).toContainText("PDF 获取失败");
  await failedCard.getByRole("button", { name: /重试获取 PDF/ }).click();
  await expect
    .poll(() => assetRetries.length, { timeout: 15000 })
    .toBeGreaterThan(1);
  expect(JSON.parse(assetRetries.at(-1)!.body)).toMatchObject({ stage: "pdf" });

  // A paper whose PDF is still coming cannot be opened, and must not be marked
  // as read just because the button was pressed.
  const waitingCard = page.locator(".daily-card", { hasText: "等待重试 PDF" });
  await expect(waitingCard).toContainText("PDF 等待重试");
  await expect(waitingCard).toContainText("等待重试：");
  const marksBefore = readMarks.length;
  await waitingCard.getByRole("button", { name: "加入 Reading List" }).click();
  await page.waitForTimeout(1500);
  expect(readMarks.length).toBe(marksBefore);

  // The list keeps itself fresh while assets are active, without a blocking
  // spinner over the rendered cards.
  const requestsBefore = paperListRequests.length;
  await expect
    .poll(() => paperListRequests.length, { timeout: 12000 })
    .toBeGreaterThan(requestsBefore);
  await expect(page.locator(".daily-card .loading")).toHaveCount(0);

  // Layout: single column and no page-level horizontal overflow on a phone.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    )
    .toBe(true);
  const columnCount = await page.evaluate(() => {
    const grid = document.querySelector(".daily-grid");
    if (!grid) return 0;
    return getComputedStyle(grid).gridTemplateColumns.split(" ").length;
  });
  expect(columnCount).toBe(1);
});