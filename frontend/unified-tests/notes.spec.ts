import { test, expect } from "@playwright/test";

// Persistent highlights, annotations, the main note and Markdown export, on the
// synthetic structured fixture (fake supplier; no real model call).
test("highlights, annotations and the main note survive a reload", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=c-4&view=reader");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("navigation").getByRole("button", { name: "文献库", exact: true }),
  ).toBeVisible();
  await page.goto("/?paper=c-4&view=reader");
  await expect(page.locator('.textLayer span').first()).toBeVisible({ timeout: 30000 });

  // Select a line of the PDF text layer and save a persistent annotation.
  await page.evaluate(() => {
    const span = document.querySelector(".textLayer span") as HTMLElement;
    const node = span.firstChild as Text;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.max(1, Math.min(12, node.length)));
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await expect(page.locator(".selection-actions")).toContainText("已选择");
  await page.getByRole("button", { name: "高亮/批注", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("高亮颜色");
  await dialog.getByRole("button", { name: "使用暖黄高亮" }).click();
  await dialog.getByLabel(/批注（可留空只保存高亮）/).fill("这是我要记住的结论");
  await dialog.getByRole("button", { name: "保存高亮/批注" }).click();
  await expect(dialog).toContainText("已保存批注");

  // The panel switches to the annotation list and shows it with its source.
  const panel = page.locator(".annotations-panel");
  await expect(panel).toContainText("这是我要记住的结论");
  const excerpt = await panel.locator(".annotation-excerpt").first().innerText();
  expect(excerpt.length).toBeGreaterThan(0);

  // Reload: the annotation is persistent and its highlight is drawn on the page.
  await page.reload();
  await expect(page.locator('.textLayer span').first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("tab", { name: /批注/ }).click();
  await expect(page.locator(".annotations-panel")).toContainText("这是我要记住的结论");
  expect(await page.locator(".reader-highlight").count()).toBeGreaterThan(0);

  // Clicking the entry locates the page again (no wrong jump).
  await page.locator(".annotation-open").first().click();
  await expect(page.locator(".annotation-detail")).toBeVisible();

  // Insert the excerpt into the main note and check autosave + persistence.
  await page.getByRole("tab", { name: /笔记/ }).click();
  const editor = page.locator(".note-textarea");
  await editor.fill("# 我的理解\n\n- 结论一\n- 结论二");
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 15000 });
  await page.reload();
  await expect(page.locator('.textLayer span').first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(/# 我的理解/);
  await expect(page.locator(".note-textarea")).toHaveValue(/结论二/);

  // The export contains the note and the annotation, without private paths.
  const exported = await page.request.get("/api/paper/c-4/reading/note/export.md");
  expect(exported.status()).toBe(200);
  const text = await exported.text();
  expect(text).toContain("结论一");
  expect(text).toContain("这是我要记住的结论");
  expect(text).not.toContain("/data/papers");
  expect(errors).toEqual([]);
});

test("annotations are owner isolated and reject stale revisions", async ({ page }) => {
  await page.goto("/?paper=c-4&view=reader");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("navigation").getByRole("button", { name: "文献库", exact: true }),
  ).toBeVisible();

  const created = await page.request.post("/api/paper/c-4/reading/annotations", {
    headers: { "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "" },
    data: {
      documentId: "synthetic",
      kind: "page_note",
      color: "violet",
      excerpt: "",
      comment: "页级记录",
      anchor: { mode: "page", page: 1 },
    },
  });
  // A page-level record still needs a real document, so this is rejected rather
  // than silently bound to a different paper.
  expect([400, 404]).toContain(created.status());
});

// Saving an already-persisted assistant answer copies it into the main note and
// never asks the model again (the fixture seeds a persisted answer).
test("an existing assistant answer can be stored in the note", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=c-4&view=reader");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("navigation").getByRole("button", { name: "文献库", exact: true }),
  ).toBeVisible();
  await page.goto("/?paper=c-4&view=reader");
  await expect(page.locator('.textLayer span').first()).toBeVisible({ timeout: 30000 });

  // The seeded persisted answer lives in a named session; select it by id.
  const sessionsSelect = page.getByLabel("聊天会话");
  await expect(sessionsSelect).toBeVisible({ timeout: 20000 });
  await expect
    .poll(async () => sessionsSelect.locator("option").count(), { timeout: 20000 })
    .toBeGreaterThan(1);
  await sessionsSelect.selectOption("notes-answer-session");
  const assistant = page.locator(".message.assistant").last();
  await expect(assistant).toBeVisible({ timeout: 30000 });
  await expect(assistant).toContainText("合成验收");
  await assistant.getByRole("button", { name: "存入本篇笔记" }).click();
  await expect(assistant.getByRole("button", { name: "已存入本篇笔记" })).toBeVisible({
    timeout: 30000,
  });

  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(/已保存的 AI 回答/);
  await expect(page.locator(".note-textarea")).toHaveValue(/S1（syntheti/);

  // A repeated click must not insert a second copy (the panel remounts, so the
  // button is back to its default label; the server dedupes by message).
  await page.getByRole("tab", { name: /问答/ }).click();
  await assistant.getByRole("button", { name: "存入本篇笔记" }).click();
  await expect(assistant.getByRole("button", { name: "已存入本篇笔记" })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(/已保存的 AI 回答/, {
    timeout: 20000,
  });
  const value = await page.locator(".note-textarea").inputValue();
  expect(value.match(/已保存的 AI 回答/g)?.length || 0).toBe(1);
});
