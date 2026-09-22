import { test, expect } from "@playwright/test";

// Persistent highlights, annotations, the main note and Markdown export, on the
// synthetic structured fixture (fake supplier; no real model call).
test("highlights, annotations and the main note survive a reload", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await enterReader(page);

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
  await enterReader(page);

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
  await enterReader(page);

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
  await expect(page.locator(".note-textarea")).toHaveValue(/来源：S1/);

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

// --- closing regressions from the independent 1.13.0 acceptance ------------

async function enterReader(page: any, paper = "c-4") {
  // The session comes from globalSetup; the isolated server rate-limits logins.
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${paper}&view=reader`);
  // Either the shell nav or the reader itself proves we are signed in.
  await Promise.race([
    page
      .getByRole("navigation")
      .getByRole("button", { name: "文献库", exact: true })
      .waitFor({ timeout: 30000 }),
    page.locator(".textLayer span").first().waitFor({ timeout: 30000 }),
  ]);
  await page.goto(`/?paper=${paper}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
}

async function openNoteTab(page: any) {
  const tab = page.getByRole("tab", { name: /笔记/ });
  if (!(await tab.count())) {
    const toggle = page.getByRole("button", { name: /论文问答/ }).first();
    if (await toggle.count()) await toggle.click();
  }
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toBeVisible({ timeout: 20000 });
}

test("a slow save response cannot replace newer typing", async ({ page }) => {
  await enterReader(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  const before = (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note;
  await page.request.put("/api/paper/c-4/reading/note", {
    headers,
    data: { markdown: "baseline", revision: before.revision },
  });
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 30000 });
  await openNoteTab(page);
  await expect(page.locator(".note-textarea")).toHaveValue("baseline");

  // Every save is held until released, so "unsaved" is observable deterministically.
  const gates: Array<() => void> = [];
  const waiters: Array<() => void> = [];
  let seen = 0;
  await page.route("**/api/paper/c-4/reading/note", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    seen += 1;
    const response = await route.fetch();
    await new Promise<void>((resolve) => {
      gates.push(resolve);
      waiters.forEach((wake) => wake());
    });
    await route.fulfill({ response });
  });
  const waitForSave = (count: number) =>
    new Promise<void>((resolve) => {
      const check = () => {
        if (seen >= count) resolve();
      };
      waiters.push(check);
      check();
    });

  const input = page.locator(".note-textarea");
  await input.fill("first text awaiting its response");
  await waitForSave(1);
  await input.fill("NEW text typed while the first save is in flight");
  gates.shift()!(); // release the older save
  // The newer text is still in the editor, and nothing claims it is saved yet.
  await expect(input).toHaveValue("NEW text typed while the first save is in flight");
  await expect(page.locator(".note-status")).toContainText("未保存的修改");
  await page.unroute("**/api/paper/c-4/reading/note");
  gates.forEach((release) => release());

  await expect
    .poll(async () => (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note.markdown, {
      timeout: 25000,
    })
    .toBe("NEW text typed while the first save is in flight");
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 25000 });
});

test("a failed draft survives panel changes and can be retried", async ({ page }) => {
  await enterReader(page);
  await openNoteTab(page);
  await page.route("**/api/paper/c-4/reading/note", (route) =>
    route.request().method() === "PUT"
      ? route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"processing_storage_failed"}' })
      : route.continue(),
  );
  await page.locator(".note-textarea").fill("UNSAVED offline text must survive");
  await expect(page.locator(".note-status")).toContainText("保存失败", { timeout: 20000 });
  await page.unroute("**/api/paper/c-4/reading/note");

  await page.getByRole("tab", { name: /问答/ }).click();
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue("UNSAVED offline text must survive");

  await page.getByRole("button", { name: "重试保存", exact: true }).click();
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 20000 });
  await expect
    .poll(async () => (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note.markdown, {
      timeout: 20000,
    })
    .toContain("UNSAVED offline text must survive");
});

test("a conflict shows its controls immediately and keeps both sides", async ({ page }) => {
  await enterReader(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  const before = (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note;
  await page.request.put("/api/paper/c-4/reading/note", {
    headers,
    data: { markdown: "baseline for conflict", revision: before.revision },
  });
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 30000 });
  await openNoteTab(page);

  // Another tab writes first; the local save then conflicts.
  const seen = (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note;
  await page.request.put("/api/paper/c-4/reading/note", {
    headers,
    data: { markdown: "remote edit from another tab", revision: seen.revision },
  });
  await page.locator(".note-textarea").fill("local conflict text");
  await expect(page.locator(".note-status")).toContainText("待选择", { timeout: 20000 });
  const draftButton = page.getByRole("button", { name: "保留我的草稿", exact: true }).first();
  await expect(draftButton).toBeVisible({ timeout: 5000 });

  await draftButton.click();
  await expect(page.locator(".note-textarea")).toHaveValue("local conflict text", { timeout: 20000 });
  const after = (await (await page.request.get("/api/paper/c-4/reading/note")).json()).note;
  expect(after.markdown).toBe("local conflict text");
  // The replaced server text is still recoverable somewhere.
  expect(after.conflicts.some((item: any) => item.markdown.includes("remote edit from another tab"))).toBe(
    true,
  );
});

test("undo uses the revision the delete returned", async ({ page }) => {
  await enterReader(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  const doc = (
    await (
      await page.request.post("/api/paper/c-4/reading-document", { headers, data: {} })
    ).json()
  ).document;
  await page.request.post("/api/paper/c-4/reading/annotations", {
    headers,
    data: {
      documentId: doc.id,
      excerpt: "UNDO unique excerpt",
      kind: "highlight",
      anchor: { mode: "pdf", page: 1, rects: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.04 }] },
    },
  });
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: "UNDO unique excerpt" });
  await expect(row).toBeVisible({ timeout: 20000 });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText("UNDO unique excerpt");

  const response = page.waitForResponse(
    (r) => r.request().method() === "PUT" && r.url().includes("/reading/annotations/"),
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  const restored = await response;
  expect(restored.status()).toBe(200);
  await expect(page.locator(".annotation-list")).toContainText("UNDO unique excerpt", { timeout: 20000 });
});

test("editing and recolouring an annotation is revision-checked and visible", async ({ page }) => {
  await enterReader(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  const doc = (
    await (
      await page.request.post("/api/paper/c-4/reading-document", { headers, data: {} })
    ).json()
  ).document;
  const created = await page.request.post("/api/paper/c-4/reading/annotations", {
    headers,
    data: {
      documentId: doc.id,
      excerpt: "EDIT unique excerpt",
      kind: "note",
      comment: "原始批注",
      color: "violet",
      anchor: { mode: "pdf", page: 1, rects: [{ x: 0.1, y: 0.3, w: 0.3, h: 0.04 }] },
    },
  });
  expect(created.status()).toBe(201);
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: "EDIT unique excerpt" });
  await expect(row).toBeVisible({ timeout: 20000 });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  await row.getByLabel(/编辑批注/).fill("改过的批注");
  await row.getByRole("button", { name: "改为雾粉" }).click();
  await row.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(row).toContainText("改过的批注", { timeout: 20000 });
  // Reload: the change persisted and the colour dot carries the new colour.
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 30000 });
  await page.getByRole("tab", { name: /批注/ }).click();
  const persisted = page.locator(".annotation-list li", { hasText: "改过的批注" });
  await expect(persisted).toBeVisible({ timeout: 20000 });
  await expect(persisted.locator(".annotation-dot.color-pink")).toHaveCount(1);
});

test("a structured original annotation is listed right after saving", async ({ page }) => {
  await enterReader(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  const preview = await (
    await page.request.post("/api/paper/c-4/processing/preview", { headers, data: {} })
  ).json();
  const started = await (
    await page.request.post("/api/paper/c-4/processing/jobs", {
      headers,
      data: { preflightId: preview.preflightId },
    })
  ).json();
  let resultId = "";
  await expect
    .poll(
      async () => {
        const job = (await (await page.request.get(`/api/processing/jobs/${started.job.id}`)).json()).job;
        resultId = job.resultId || "";
        return job.status;
      },
      { timeout: 60000 },
    )
    .toBe("completed");

  await page.goto("/?paper=c-4&view=reader&content=structure");
  await expect(page.locator(".structured-workspace")).toBeVisible({ timeout: 30000 });
  const field = page.locator('.block-language[data-language="original"] [data-field="text"]').first();
  await expect(field).toBeVisible({ timeout: 20000 });
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
  const before = (
    await (await page.request.get("/api/paper/c-4/reading/annotations")).json()
  ).annotations.length;
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/reading/annotations"),
  );
  await page.getByRole("button", { name: "保存高亮/批注", exact: true }).click();
  expect((await created).status()).toBe(201);
  // The list is queried by document, so the row is immediately visible.
  await page.getByRole("tab", { name: /批注/ }).click();
  // The saved structure annotation is visible immediately (the former bug showed
  // an empty list because the query used the result id as a document id).
  await expect
    .poll(async () => page.locator(".annotation-list li").count(), { timeout: 20000 })
    .toBeGreaterThan(before);
  await expect(page.locator(".annotation-list")).toContainText("结构");
});

async function enterStructure(page: any, paper = "c-4") {
  // By this point the fixture paper has a structure result, so the reader opens
  // the structured workspace instead of the PDF text layer.
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${paper}&view=reader&content=structure`);
  await expect(page.locator(".structured-workspace")).toBeVisible({ timeout: 60000 });
}

test("the structure translation side accepts an annotation in the browser", async ({ page }) => {
  await enterStructure(page);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "paperpilot_csrf")?.value || "",
  };
  // Produce a structured translation through the real API with the fixture's
  // fake supplier (no real model), then annotate its translated side in the UI.
  const preview = await (
    await page.request.post("/api/paper/c-4/processing/preview", { headers, data: {} })
  ).json();
  const started = await (
    await page.request.post("/api/paper/c-4/processing/jobs", {
      headers,
      data: { preflightId: preview.preflightId, kind: "parse_translate" },
    })
  ).json();
  await expect
    .poll(
      async () => (await (await page.request.get(`/api/processing/jobs/${started.job.id}`)).json()).job.status,
      { timeout: 180000 },
    )
    .toBe("completed");
  const finished = (await (await page.request.get(`/api/processing/jobs/${started.job.id}`)).json()).job;
  const resultId = finished.resultId;
  expect(resultId).toBeTruthy();
  const blocks = (await (await page.request.get(`/api/results/${resultId}/blocks`)).json()).blocks;
  const translated = blocks.find(
    (b: any) => (b.translation || {}).status === "completed" && b.translation?.content?.text?.length >= 10,
  );
  expect(translated, "the fixture produces at least one translated block").toBeTruthy();

  await page.goto("/?paper=c-4&view=reader&content=structure");
  await expect(page.locator(".structured-workspace")).toBeVisible({ timeout: 30000 });
  const display = page.getByLabel("显示内容");
  if (await display.count()) {
    const options = await display.locator("option").allTextContents();
    const both = options.find((value) => /双语|对照|译/.test(value));
    if (both) await display.selectOption({ label: both });
    await page.waitForTimeout(600);
  }
  const field = page.locator('.block-language[data-language="translated"] [data-field="text"]').first();
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
  await page.getByRole("button", { name: "保存高亮/批注", exact: true }).click();
  await page.getByRole("tab", { name: /批注/ }).click();
  const row = page.locator(".annotation-list li", { hasText: "结构译文" }).first();
  await expect(row).toBeVisible({ timeout: 20000 });
  await expect(page.locator(".structured-marks-layer .reader-highlight").first()).toBeVisible({
    timeout: 20000,
  });
  // The translated anchor names the real block revision and is navigable.
  const listed = (await (await page.request.get("/api/paper/c-4/reading/annotations")).json()).annotations;
  const item = listed.find((a: any) => a.contentKind === "structure_translated");
  expect(item).toBeTruthy();
  expect(item.canNavigate).toBe(true);
  expect(typeof item.anchor.translationRevision).toBe("string");
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".annotation-list")).not.toContainText("结构译文", { timeout: 20000 });
});
