import { test, expect } from "@playwright/test";

// Read-only rendering check for the reading-notes panel across viewports and themes.
const TOKEN = process.env.IPAPER_MAINT_TOKEN || "";
const CSRF = process.env.IPAPER_MAINT_CSRF || "";
const PAPER = process.env.IPAPER_ACCEPT_PAPER || "";
const OUT = process.env.IPAPER_ACCEPT_OUT || "/tmp";

test.use({ baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191" });

test.beforeEach(async ({ context }) => {
  test.skip(!TOKEN || !PAPER, "maintenance session and paper are required");
  await context.addCookies([
    { name: "paperpilot_session", value: TOKEN, url: "http://127.0.0.1:7191" },
    { name: "paperpilot_csrf", value: CSRF, url: "http://127.0.0.1:7191" },
  ]);
});

const VIEWPORTS = [
  { width: 1920, height: 1080, name: "1920" },
  { width: 1440, height: 1000, name: "1440" },
  { width: 1280, height: 900, name: "1280" },
  { width: 390, height: 900, name: "390" },
];

for (const theme of ["light", "dark"] as const) {
  for (const viewport of VIEWPORTS) {
    test(`notes panel renders at ${viewport.width} in ${theme}`, async ({ page }) => {
      test.skip(theme === "dark" && viewport.width !== 1440, "dark is checked at 1440 only");
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto(`/?paper=${PAPER}&view=reader`);
      await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
      if (theme === "dark") {
        await page.getByRole("button", { name: "切换浅深主题", exact: true }).click();
        await page.waitForTimeout(600);
      }
      if (viewport.width <= 640) {
        const toggle = page.getByRole("button", { name: /论文问答/ }).first();
        if (await toggle.count()) await toggle.click();
      }
      await page.getByRole("tab", { name: /笔记/ }).click();
      await expect(page.locator(".note-textarea")).toBeVisible({ timeout: 20000 });
      const box = (await page.locator(".note-textarea").boundingBox())!;
      expect(box.width).toBeGreaterThan(120);
      expect(box.height).toBeGreaterThan(80);
      await page.getByRole("tab", { name: /批注/ }).click();
      await expect(page.locator(".annotations-toolbar")).toBeVisible();
      const scroll = await page.evaluate(() => ({
        docWidth: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
      }));
      // No horizontal overflow of the whole page at any width.
      expect(scroll.docWidth).toBeLessThanOrEqual(scroll.viewport + 2);
      await page.screenshot({
        path: `${OUT}/notes-${theme}-${viewport.name}.png`,
        fullPage: false,
      });
    });
  }
}
