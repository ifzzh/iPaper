import { test, expect, type Page } from "@playwright/test";

// Closeout acceptance against the deployed service: geometry of a PDF highlight,
// a structure annotation saved through the real UI, and the note save/conflict
// path. Only records this spec creates are removed again (by id).
const TOKEN = process.env.IPAPER_MAINT_TOKEN || "";
const CSRF = process.env.IPAPER_MAINT_CSRF || "";
const PAPER = process.env.IPAPER_ACCEPT_PAPER || "";
const TRANSLATED_PAPER = process.env.IPAPER_ACCEPT_TRANSLATED_PAPER || "";
const STRUCTURE_PAPER = process.env.IPAPER_ACCEPT_STRUCTURE_PAPER || "";
const STRUCTURE_RESULT = process.env.IPAPER_ACCEPT_STRUCTURE_RESULT || "";
const MARK = "收尾浏览器验收";

test.use({ baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191" });

test.beforeEach(async ({ context }) => {
  test.skip(!TOKEN || !PAPER, "maintenance session and paper are required");
  await context.addCookies([
    { name: "paperpilot_session", value: TOKEN, url: "http://127.0.0.1:7191" },
    { name: "paperpilot_csrf", value: CSRF, url: "http://127.0.0.1:7191" },
  ]);
});

function headers(_page: Page) {
  return { "X-CSRF-Token": CSRF };
}

async function selectFirstLine(page: Page, selector = ".textLayer span") {
  return page.evaluate((sel) => {
    const span = document.querySelector(sel) as HTMLElement;
    const node = span.firstChild as Text;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.max(1, Math.min(12, node.length)));
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    const rect = range.getClientRects()[0];
    return { text: range.toString(), rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height } };
  }, selector);
}

test("a PDF highlight lands on the selected line, not just somewhere on the page", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  const selected = await selectFirstLine(page);
  await page.getByRole("button", { name: "高亮/批注", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/批注/).fill(`${MARK} 几何`);
  await dialog.getByRole("button", { name: "保存高亮/批注" }).click();
  await expect(dialog).toContainText("已保存批注");
  await page.keyboard.press("Escape");

  const mark = page.locator(".reader-highlight").first();
  await expect(mark).toBeVisible({ timeout: 20000 });
  const box = (await mark.boundingBox())!;
  // The mark must overlap the text it was created from (normalised geometry).
  const overlapX = Math.max(0, Math.min(box.x + box.width, selected.rect.x + selected.rect.w) - Math.max(box.x, selected.rect.x));
  const overlapY = Math.max(0, Math.min(box.y + box.height, selected.rect.y + selected.rect.h) - Math.max(box.y, selected.rect.y));
  expect(overlapX).toBeGreaterThan(selected.rect.w * 0.3);
  expect(overlapY).toBeGreaterThan(0);
  // Zoom 150%: the overlap survives the re-layout.
  await page.getByLabel("缩放").selectOption("1.5");
  await page.waitForTimeout(900);
  const zoomed = (await page.locator(".reader-highlight").first().boundingBox())!;
  expect(zoomed.width).toBeGreaterThan(box.width * 1.1);

  // Remove exactly this record.
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: `${MARK} 几何` });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText(`${MARK} 几何`, { timeout: 20000 });
});

test("the translated layout document accepts a highlight through the real UI", async ({ page }) => {
  test.skip(!TRANSLATED_PAPER, "no translated document available");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${TRANSLATED_PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  // Switch the reading content to the layout translation when the control exists.
  const content = page.getByLabel("阅读内容");
  if (await content.count()) {
    const options = await content.locator("option").allTextContents();
    const translated = options.find((value) => /译/.test(value));
    if (translated) await content.selectOption({ label: translated });
    await page.waitForTimeout(800);
  }
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await selectFirstLine(page);
  await page.getByRole("button", { name: "高亮/批注", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/批注/).fill(`${MARK} 版式译文`);
  await dialog.getByRole("button", { name: "保存高亮/批注" }).click();
  await expect(dialog).toContainText("已保存批注");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: `${MARK} 版式译文` });
  await expect(row).toBeVisible({ timeout: 20000 });
  await expect(row).toContainText("版式译文");
  await expect(row).not.toContainText("不可用");
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText(`${MARK} 版式译文`, { timeout: 20000 });
});

test("a structure annotation is saved, listed and marked through the real UI", async ({ page }) => {
  test.skip(!STRUCTURE_PAPER || !STRUCTURE_RESULT, "no structure result available");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${STRUCTURE_PAPER}&view=reader&content=structure`);
  await expect(page.locator(".structured-workspace")).toBeVisible({ timeout: 60000 });
  const field = page.locator('.block-language[data-language="original"] [data-field="text"]').first();
  await expect(field).toBeVisible({ timeout: 30000 });
  await field.evaluate((element) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode()!;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(node.textContent!.length, 12));
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  const selected = await page.locator(".selection-actions").innerText();
  expect(selected).toContain("已选择");
  await page.locator(".selection-comment").fill(`${MARK} 结构`);
  await page.getByRole("button", { name: "保存高亮/批注", exact: true }).click();
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: `${MARK} 结构` });
  await expect(row).toBeVisible({ timeout: 20000 });
  // The structured mark is drawn over the real block text.
  await expect(page.locator(".structured-marks-layer .reader-highlight").first()).toBeVisible({
    timeout: 20000,
  });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText(`${MARK} 结构`, { timeout: 20000 });
});

test("the note keeps new typing, survives a reload and resolves a conflict by choice", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  const tab = page.getByRole("tab", { name: /笔记/ });
  if (!(await tab.count())) {
    const toggle = page.getByRole("button", { name: /论文问答/ }).first();
    if (await toggle.count()) await toggle.click();
  }
  await page.getByRole("tab", { name: /笔记/ }).click();
  const input = page.locator(".note-textarea");
  await expect(input).toBeVisible({ timeout: 20000 });
  const baseline = await input.inputValue();

  await input.fill(`${baseline}\n${MARK} 新输入` .trim());
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 20000 });
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(new RegExp(`${MARK} 新输入`), { timeout: 20000 });

  // Another tab writes first, then this page saves: a conflict must be offered
  // immediately, with working buttons, and both sides must survive.
  const note = (await (await page.request.get(`/api/paper/${PAPER}/reading/note`)).json()).note;
  await page.request.put(`/api/paper/${PAPER}/reading/note`, {
    headers: headers(page),
    data: { markdown: `${MARK} 另一标签`, revision: note.revision },
  });
  await page.locator(".note-textarea").fill(`${MARK} 我的草稿`);
  await expect(page.locator(".note-status")).toContainText("待选择", { timeout: 20000 });
  const keepDraft = page.getByRole("button", { name: "保留我的草稿", exact: true }).first();
  await expect(keepDraft).toBeVisible({ timeout: 5000 });
  await keepDraft.click();
  await expect(page.locator(".note-textarea")).toHaveValue(`${MARK} 我的草稿`, { timeout: 20000 });
  const after = (await (await page.request.get(`/api/paper/${PAPER}/reading/note`)).json()).note;
  expect(after.markdown).toBe(`${MARK} 我的草稿`);
  expect(after.conflicts.some((item: any) => item.markdown.includes(`${MARK} 另一标签`))).toBe(true);
});
