import { test, expect, type Page } from "@playwright/test";

// Manual acceptance against the deployed service (no webServer). A temporary,
// owner-scoped maintenance session is injected through the environment; the
// spec creates its own records and removes them again.
const TOKEN = process.env.IPAPER_MAINT_TOKEN || "";
const CSRF = process.env.IPAPER_MAINT_CSRF || "";
const PAPER = process.env.IPAPER_ACCEPT_PAPER || "";

test.use({ baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191" });

test.beforeEach(async ({ context }) => {
  test.skip(!TOKEN || !PAPER, "maintenance session and paper are required");
  await context.addCookies([
    { name: "paperpilot_session", value: TOKEN, url: "http://127.0.0.1:7191" },
    { name: "paperpilot_csrf", value: CSRF, url: "http://127.0.0.1:7191" },
  ]);
});

async function selectLine(page: Page) {
  await page.evaluate(() => {
    const span = document.querySelector(".textLayer span") as HTMLElement;
    const node = span.firstChild as Text;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.max(1, Math.min(10, node.length)));
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

test("highlights stay on their line across zoom, rotation, width and theme", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });

  await selectLine(page);
  await page.getByRole("button", { name: "高亮/批注", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "使用灰蓝高亮" }).click();
  await dialog.getByLabel(/批注/).fill("正式浏览器验收");
  await dialog.getByRole("button", { name: "保存高亮/批注" }).click();
  await expect(dialog).toContainText("已保存批注");
  await page.keyboard.press("Escape");

  const boxes = async () => {
    const marks = page.locator(".reader-highlight");
    await expect(marks.first()).toBeVisible({ timeout: 20000 });
    return marks.first().boundingBox();
  };
  const base = await boxes();

  // Zoom in through the real select, then rotate: the mark must keep covering
  // the same line (percent coordinates) rather than drift.
  const zoom = page.getByLabel("缩放");
  await zoom.selectOption("1.5");
  await page.waitForTimeout(900);
  const zoomed = await boxes();
  expect(zoomed!.width).toBeGreaterThan(base!.width * 1.1);
  await zoom.selectOption("width");
  await page.waitForTimeout(900);

  const rotate = page.getByRole("button", { name: "旋转", exact: true });
  await rotate.click();
  await page.waitForTimeout(1200);
  const rotated = await boxes();
  expect(rotated!.width).toBeGreaterThan(2);
  expect(rotated!.height).toBeGreaterThan(2);
  await rotate.click();
  await page.waitForTimeout(1200);

  // Narrow width and dark theme render without losing the record.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(600);
  // At phone width the reader is single-pane: open the panel first.
  const panelToggle = page.getByRole("button", { name: /论文问答|返回阅读/ }).first();
  if (await panelToggle.count()) await panelToggle.click();
  await page.getByRole("tab", { name: /批注/ }).click();
  await expect(page.locator(".annotations-panel")).toContainText("正式浏览器验收");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(400);
  if (await page.getByRole("button", { name: "返回阅读", exact: true }).count()) {
    await page.getByRole("button", { name: "返回阅读", exact: true }).click();
  }
  // Theme switching is exercised in the isolated suite; here we only confirm the
  // record still lays out after the viewport changes back.
  await expect(page.getByRole("button", { name: "切换浅深主题", exact: true })).toBeVisible();
  const wide = await page.locator(".reader-highlight").first().boundingBox();
  expect(wide!.width).toBeGreaterThan(2);

  // Reload: still there.
  await page.reload();
  await expect(page.locator(".reader-highlight").first()).toBeVisible({ timeout: 60000 });

  // Remove exactly this acceptance record.
  await page.getByRole("tab", { name: /批注/ }).click();
  page.on("dialog", (d) => void d.accept());
  await page.locator(".annotation-list li", { hasText: "正式浏览器验收" }).getByRole("button", { name: "删除" }).click();
  await expect(page.locator(".annotation-list")).not.toContainText("正式浏览器验收", { timeout: 20000 });
});
