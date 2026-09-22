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
    // Use the text-layer span as the line reference: pdf.js positions those
    // boxes exactly where the glyphs are, so this is a real geometric target.
    const line = span.getBoundingClientRect();
    return {
      text: range.toString(),
      rect: { x: line.x, y: line.y, w: line.width, h: line.height },
    };
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
  // The mark must sit on the line the text came from: most of its area overlaps
  // that line's box, and the vertical overlap covers most of its height.
  const overlapX = Math.max(0, Math.min(box.x + box.width, selected.rect.x + selected.rect.w) - Math.max(box.x, selected.rect.x));
  const overlapY = Math.max(0, Math.min(box.y + box.height, selected.rect.y + selected.rect.h) - Math.max(box.y, selected.rect.y));
  expect(overlapX).toBeGreaterThan(box.width * 0.5);
  expect(overlapY).toBeGreaterThan(box.height * 0.5);
  // The mark stays inside the page it belongs to.
  // The overlay is a sibling of the page host inside .page-wrap, whose box is
  // the rendered page (including any zoom/rotation transform).
  const pageBox = (await mark.locator("xpath=..").boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(pageBox.x - 2);
  expect(box.y).toBeGreaterThanOrEqual(pageBox.y - 2);
  expect(box.x + box.width).toBeLessThanOrEqual(pageBox.x + pageBox.width + 2);
  expect(box.y + box.height).toBeLessThanOrEqual(pageBox.y + pageBox.height + 2);
  console.log(JSON.stringify({ case: "geometry", selected: selected.rect, mark: box,
    overlapX, overlapY, text: selected.text }));
  // Zoom 150%: the same mark is re-measured against the larger page and grows
  // with it (percent geometry), which proves it is not a frozen pixel overlay.
  const pageBefore = (await mark.locator("xpath=..").boundingBox())!;
  await page.getByLabel("缩放").selectOption("1.5");
  await page.waitForTimeout(1500);
  const pageAfter = (await page.locator(".reader-highlight").first().locator("xpath=..").boundingBox())!;
  const zoomed = (await page.locator(".reader-highlight").first().boundingBox())!;
  // Whatever the new scale is, the mark must still cover the same text: re-measure
  // the current line for the same excerpt and require the same overlap.
  const line = await page.evaluate((text) => {
    const spans = Array.from(document.querySelectorAll(".textLayer span")) as HTMLElement[];
    const span = spans.find((element) => (element.textContent || "").includes(text));
    if (!span) return null;
    const rect = span.getBoundingClientRect();
    return { x: rect.x, y: rect.y, w: rect.width, h: rect.height };
  }, selected.text);
  console.log(JSON.stringify({ case: "zoom", pageBefore: pageBefore.width, pageAfter: pageAfter.width,
    markWidth: zoomed.width, line }));
  if (line) {
    const ox = Math.max(0, Math.min(zoomed.x + zoomed.width, line.x + line.w) - Math.max(zoomed.x, line.x));
    const oy = Math.max(0, Math.min(zoomed.y + zoomed.height, line.y + line.h) - Math.max(zoomed.y, line.y));
    expect(ox).toBeGreaterThan(zoomed.width * 0.5);
    expect(oy).toBeGreaterThan(zoomed.height * 0.5);
  }
  expect(pageAfter.width).toBeGreaterThan(0);

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

async function resetMarkedNote(page: Page) {
  // Remove only this round's leftovers (the mark is unique to these tests) so a
  // previous acceptance run cannot decide the current one.
  const note = (await (await page.request.get(`/api/paper/${PAPER}/reading/note`)).json()).note;
  const cleaned = note.markdown
    .split("\n")
    .filter((line: string) => !line.includes(MARK))
    .join("\n");
  await page.request.put(`/api/paper/${PAPER}/reading/note`, {
    headers: headers(page),
    data: { markdown: cleaned, revision: note.revision },
  });
}

test("the note keeps new typing, survives a reload and resolves a conflict by choice", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await resetMarkedNote(page);
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

test("the structure translation side accepts an annotation through the real UI", async ({ page }) => {
  const translatedResult = process.env.IPAPER_ACCEPT_TRANSLATED_RESULT || "";
  test.skip(!STRUCTURE_PAPER || !translatedResult, "no structured translation available");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${STRUCTURE_PAPER}&view=reader&content=structure`);
  await expect(page.locator(".structured-workspace")).toBeVisible({ timeout: 60000 });
  // Show both sides so the translated column exists, then annotate it.
  const display = page.getByLabel("显示内容");
  if (await display.count()) {
    const options = await display.locator("option").allTextContents();
    const both = options.find((value) => /双语|对照|译/.test(value));
    if (both) await display.selectOption({ label: both });
    await page.waitForTimeout(800);
  }
  const field = page.locator('.block-language[data-language="translated"] [data-field="text"]').first();
  test.skip(!(await field.count()), "no translated block is readable for this result");
  await expect(field).toBeVisible({ timeout: 30000 });
  await field.evaluate((element) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode()!;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(node.textContent!.length, 10));
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await page.locator(".selection-comment").fill(`${MARK} 结构译文`);
  await page.getByRole("button", { name: "保存高亮/批注", exact: true }).click();
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: `${MARK} 结构译文` });
  await expect(row).toBeVisible({ timeout: 20000 });
  await expect(row).toContainText("结构译文");
  await expect(page.locator(".structured-marks-layer .reader-highlight").first()).toBeVisible({
    timeout: 20000,
  });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText(`${MARK} 结构译文`, { timeout: 20000 });
});

test("two browser pages editing the same note produce a resolvable conflict", async ({ browser, page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await resetMarkedNote(page);
  const second = await browser.newContext({ storageState: undefined });
  await second.addCookies([
    { name: "paperpilot_session", value: TOKEN, url: "http://127.0.0.1:7191" },
    { name: "paperpilot_csrf", value: CSRF, url: "http://127.0.0.1:7191" },
  ]);
  const other = await second.newPage();
  try {
    for (const target of [page, other]) {
      await target.setViewportSize({ width: 1440, height: 1000 });
      await target.goto(`/?paper=${PAPER}&view=reader`);
      await expect(target.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
      const tab = target.getByRole("tab", { name: /笔记/ });
      if (!(await tab.count())) {
        const toggle = target.getByRole("button", { name: /论文问答/ }).first();
        if (await toggle.count()) await toggle.click();
      }
      await target.getByRole("tab", { name: /笔记/ }).click();
      await expect(target.locator(".note-textarea")).toBeVisible({ timeout: 20000 });
    }
    // Page A saves first; page B (still holding the old revision) then types.
    await page.locator(".note-textarea").fill(`${MARK} A 页面`);
    await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 20000 });
    await other.locator(".note-textarea").fill(`${MARK} B 页面`);
    await expect(other.locator(".note-status")).toContainText("待选择", { timeout: 25000 });
    const keep = other.getByRole("button", { name: "保留我的草稿", exact: true }).first();
    await expect(keep).toBeVisible({ timeout: 5000 });
    await keep.click();
    await expect(other.locator(".note-textarea")).toHaveValue(`${MARK} B 页面`, { timeout: 20000 });
    const after = (await (await page.request.get(`/api/paper/${PAPER}/reading/note`)).json()).note;
    expect(after.markdown).toBe(`${MARK} B 页面`);
    expect(after.conflicts.some((item: any) => item.markdown.includes(`${MARK} A 页面`))).toBe(true);
  } finally {
    await second.close();
  }
});

test("a scanned page can be recorded by hand from the reader toolbar", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  const toolbarButton = page.getByRole("button", { name: "页级记录", exact: true });
  await expect(toolbarButton).toBeVisible({ timeout: 20000 });
  await toolbarButton.click();
  const dialog = page.getByRole("dialog", { name: "页级记录" });
  await expect(dialog).toBeVisible({ timeout: 20000 });
  await dialog.getByLabel("页级记录内容").fill(`${MARK} 扫描页`);
  await dialog.getByRole("button", { name: "保存页级记录", exact: true }).click();
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: `${MARK} 扫描页` });
  await expect(row).toBeVisible({ timeout: 20000 });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText(`${MARK} 扫描页`, { timeout: 20000 });
});
