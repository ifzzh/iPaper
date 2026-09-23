import { test, expect } from "@playwright/test";

// Real-image acceptance for V1/V2/V5 on the deployed 1.13.2 container: the
// browser loads the image's own static build. Only this spec's records are made,
// and they are removed again.
const TOKEN = process.env.IPAPER_MAINT_TOKEN || "";
const CSRF = process.env.IPAPER_MAINT_CSRF || "";
const PAPER = process.env.IPAPER_ACCEPT_PAPER || "";
const MARK = "1.13.2 镜像验收";

test.use({ baseURL: process.env.IPAPER_ACCEPT_BASE || "http://127.0.0.1:7191" });

const PAPER_ID = PAPER || "c-4";

test.beforeEach(async ({ context }) => {
  if (TOKEN) {
    // Deployed-image run: inject an owner-scoped maintenance session.
    for (const url of ["http://127.0.0.1:7191", "http://127.0.0.2:7191"]) {
      await context.addCookies([
        { name: "paperpilot_session", value: TOKEN, url },
        { name: "paperpilot_csrf", value: CSRF, url },
      ]);
    }
  } else {
    test.skip(!process.env.IPAPER_ISOLATED, "maintenance session or isolated stack required");
  }
});

const headers = { "X-CSRF-Token": CSRF || (process.env.IPAPER_ISOLATED ? "isolated" : "") };

async function enterNote(page: any) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?paper=${PAPER_ID}&view=reader`);
  // Wait for the shell (or the reader itself), then load the reader directly so
  // the HTTP cache cannot serve a half-mounted shell on the first navigation.
  await Promise.race([
    page
      .getByRole("navigation")
      .getByRole("button", { name: "文献库", exact: true })
      .waitFor({ timeout: 30000 }),
    page.locator(".textLayer span").first().waitFor({ timeout: 30000 }),
  ]);
  await page.goto(`/?paper=${PAPER_ID}&view=reader`);
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  const tab = page.getByRole("tab", { name: /笔记/ });
  if (!(await tab.count())) {
    const toggle = page.getByRole("button", { name: /论文问答/ }).first();
    if (await toggle.count()) await toggle.click();
  }
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toBeVisible({ timeout: 20000 });
}

async function resetNote(page: any) {
  const current = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  const cleaned = current.markdown
    .split("\n")
    .filter((line: string) => !line.includes(MARK))
    .join("\n");
  const reset = await page.request.put(`/api/paper/${PAPER_ID}/reading/note`, {
    headers,
    data: { markdown: cleaned, revision: current.revision },
  });
  expect(reset.status()).toBe(200);
  return cleaned;
}

test("V1 on the image: typing during a slow save survives and wins", async ({ page }) => {
  await enterNote(page);
  const baseline = await resetNote(page);
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(baseline);

  let release!: () => void;
  let captured!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const ready = new Promise<void>((r) => (captured = r));
  let first = true;
  await page.route("**/reading/note", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    if (first) {
      first = false;
      const response = await route.fetch();
      captured();
      await gate;
      return route.fulfill({ response });
    }
    return route.continue();
  });
  await page.locator(".note-textarea").fill(`${MARK} A 较早文本`);
  await ready;
  await page.locator(".note-textarea").fill(`${MARK} B 最新文本\n\n- 要点一\n- 要点二\n\n第二段中文。`);
  release();
  await page.getByRole("tab", { name: /问答/ }).click();
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(
    `${MARK} B 最新文本\n\n- 要点一\n- 要点二\n\n第二段中文。`,
    { timeout: 20000 },
  );
  await page.unroute("**/reading/note");
  await expect
    .poll(async () => (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note.markdown, {
      timeout: 30000,
    })
    .toBe(`${MARK} B 最新文本\n\n- 要点一\n- 要点二\n\n第二段中文。`);
  const noteNow = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  console.log(JSON.stringify({ case: "image-v1", editor: await page.locator(".note-textarea").inputValue(),
    status: await page.locator(".note-status").innerText(), server: noteNow.markdown,
    revision: noteNow.revision, conflicts: noteNow.conflicts.map((c: any) => ({ base: c.baseRevision, current: c.currentRevision, markdown: c.markdown })) }));
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 30000 });
});

test("V2 on the image: a real conflict offers working decisions", async ({ page }) => {
  await enterNote(page);
  await resetNote(page);
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  const seen = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  await page.request.put(`/api/paper/${PAPER_ID}/reading/note`, {
    headers,
    data: { markdown: `${MARK} 另一标签的远端文本`, revision: seen.revision },
  });
  await page.locator(".note-textarea").fill(`${MARK} 我的草稿`);
  await expect(page.locator(".note-status")).toContainText("待选择", { timeout: 20000 });
  const keepDraft = page.getByRole("button", { name: "保留我的草稿", exact: true }).first();
  await expect(keepDraft).toBeVisible({ timeout: 5000 });
  // Keep typing while the decision is pending: the decision must use this text.
  await page.locator(".note-textarea").fill(`${MARK} 我的草稿加上之后的输入`);
  await keepDraft.click();
  await expect(page.locator(".note-textarea")).toHaveValue(`${MARK} 我的草稿加上之后的输入`, {
    timeout: 20000,
  });
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 20000 });
  const after = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  expect(after.markdown).toBe(`${MARK} 我的草稿加上之后的输入`);
  expect(
    after.conflicts.some((item: any) => item.markdown.includes(`${MARK} 另一标签的远端文本`)),
  ).toBe(true);

  // The other direction: keep the server version and see the editor follow.
  const before = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  await page.request.put(`/api/paper/${PAPER_ID}/reading/note`, {
    headers,
    data: { markdown: `${MARK} 服务器版本`, revision: before.revision },
  });
  await page.locator(".note-textarea").fill(`${MARK} 将被丢弃的本地草稿`);
  const keepServer = page.getByRole("button", { name: "保留服务器版本", exact: true }).first();
  await expect(keepServer).toBeVisible({ timeout: 20000 });
  // A decision can legitimately be refused when the note moved on while the user
  // was looking at it (the server then preserves the newer content and the UI is
  // expected to offer the decision again). Accept at most one such refusal.
  let decided = false;
  for (let attempt = 0; attempt < 3 && !decided; attempt += 1) {
    const clicked = page.getByRole("button", { name: "保留服务器版本", exact: true }).first();
    await expect(clicked).toBeVisible({ timeout: 20000 });
    const response = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.url().includes("/reading/note/conflicts/"),
    );
    await clicked.click();
    const settled = await response;
    console.log(JSON.stringify({ case: "image-decision", attempt, status: settled.status(),
      body: settled.request().postData(), response: (await settled.text()).slice(0, 160) }));
    if (settled.status() === 200) {
      decided = true;
      break;
    }
    await page.waitForTimeout(800);
  }
  expect(decided, "the keep-server decision eventually applies").toBe(true);
  await expect(page.locator(".note-textarea")).toHaveValue(`${MARK} 服务器版本`, { timeout: 20000 });
  await expect(page.locator(".note-status")).toContainText("已保存", { timeout: 20000 });
});

test("V5 on the image: an excerpt links back to its source", async ({ page }) => {
  await enterNote(page);
  await resetNote(page);
  const doc = (
    await (await page.request.post(`/api/paper/${PAPER_ID}/reading-document`, { headers, data: {} })).json()
  ).document;
  const created = await page.request.post(`/api/paper/${PAPER_ID}/reading/annotations`, {
    headers,
    data: {
      documentId: doc.id,
      kind: "highlight",
      excerpt: `${MARK} 摘录`,
      color: "violet",
      anchor: { mode: "pdf", page: 1, rects: [{ x: 0.1, y: 0.12, w: 0.5, h: 0.04 }] },
    },
  });
  expect(created.status()).toBe(201);
  const annotation = (await created.json()).annotation;
  const current = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  const inserted = await page.request.post(`/api/paper/${PAPER_ID}/reading/note/excerpts`, {
    headers,
    data: { annotationId: annotation.id, revision: current.revision },
  });
  expect(inserted.status()).toBe(200);

  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(new RegExp(MARK));
  await page.locator(".note-sources summary").click();
  const backlink = page.locator(".note-sources button", { hasText: "回到来源" }).first();
  await expect(backlink).toBeVisible({ timeout: 20000 });
  await backlink.click();
  await expect(page.locator(".annotation-detail")).toBeVisible({ timeout: 20000 });
  await expect(page.locator(".annotation-detail")).toContainText("已定位到来源");
});

test("V3 on the image: many note saves no longer exhaust a general limit", async ({ page }) => {
  await enterNote(page);
  const baseline = await resetNote(page);
  await page.reload();
  await expect(page.locator(".textLayer span").first()).toBeVisible({ timeout: 60000 });
  await page.getByRole("tab", { name: /笔记/ }).click();
  await expect(page.locator(".note-textarea")).toHaveValue(baseline);
  const started = Date.now();
  for (let index = 0; index < 40; index += 1) {
    await page.locator(".note-textarea").fill(`${MARK} 连续保存 ${index}`);
    await page.waitForTimeout(1300);
  }
  const note = (await (await page.request.get(`/api/paper/${PAPER_ID}/reading/note`)).json()).note;
  expect(note.markdown).toBe(`${MARK} 连续保存 39`);
  console.log(JSON.stringify({ case: "image-save-pace", saves: 40, seconds: Math.round((Date.now() - started) / 1000) }));
});
