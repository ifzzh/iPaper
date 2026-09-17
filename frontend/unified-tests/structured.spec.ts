import { test, expect } from "@playwright/test";

test("dual translation source workflow with isolated fake suppliers", async ({
  page,
}) => {
  const errors: string[] = [],
    csp: string[] = [],
    external: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/.test(m.text())) csp.push(m.text());
  });
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.2:7191/") &&
      !r.url().startsWith("data:")
    )
      external.push(r.url());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=c-4&view=reader");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page
      .getByRole("navigation")
      .getByRole("button", { name: "文献库", exact: true }),
  ).toBeVisible();
  await page.goto("/?paper=c-4&view=reader");
  await expect(
    page.locator('.pdf-page[data-rendered="true"]').first(),
  ).toBeVisible();
  await expect(page.locator(".textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await page.getByLabel("阅读内容").selectOption("structure");
  await page.getByRole("button", { name: "生成结构内容", exact: true }).click();
  await expect(page.getByText("翻译模型：fixture")).toBeVisible();
  await page.getByRole("button", { name: "开始生成", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.getByRole("button", { name: "关闭", exact: true }).first().click();
  // Task completion updates the current reader without a reload workaround.
  await expect(page.locator(".structure-block").first()).toBeVisible();
  await expect(page.locator(".block-pair.bilingual").first()).toBeVisible();
  await expect(
    page.locator(".block-language").filter({ hasText: "合成译文" }).first(),
  ).toBeVisible();
  await expect(page.locator("math").first()).toBeVisible();
  const notation = page.locator(".structure-paragraph sup").first();
  await expect(notation).toHaveText("*");
  await expect(page.locator(".structure-paragraph sub").first()).toHaveText(
    "2",
  );
  await notation.evaluate((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await notation.dispatchEvent("mouseup");
  await page.getByRole("button", { name: "带来源提问", exact: true }).click();
  await expect(page.locator(".excerpt-card sup")).toHaveText("*");
  await page.getByLabel("移除引用").click();
  await page
    .getByRole("button", { name: "原文对照", exact: true })
    .first()
    .click();
  await expect(
    page
      .locator('.structure-comparison .pdf-page[data-rendered="true"]')
      .first(),
  ).toBeVisible();
  await expect(
    page.locator(".structure-comparison .pdf-source-highlight").first(),
  ).toBeVisible();
  await expect(page.locator(".structure-chat")).toHaveCount(0);
  await page.screenshot({
    path: "../.devnotes/dual-translation-compare-1440-light.png",
  });
  await page.getByLabel("关闭原文对照").click();
  await page
    .getByRole("button", { name: "查看来源", exact: true })
    .first()
    .click();
  await expect(
    page.locator('.pdf-page[data-rendered="true"]').first(),
  ).toBeVisible();
  await expect(page.locator(".pdf-source-highlight").first()).toBeVisible();
  await page.getByRole("button", { name: "返回结构内容", exact: true }).click();
  await expect(page.locator(".structure-block").first()).toBeVisible();
  // Block retranslation is explicit and saves a new revision.
  await page.getByRole("button", { name: "重译", exact: true }).first().click();
  await expect(
    page.getByRole("button", { name: "确认重译", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "确认重译", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.getByRole("button", { name: "关闭", exact: true }).first().click();
  const paragraph = page.locator(".structure-paragraph").first();
  await paragraph.evaluate((el) => {
    const text = el.querySelector("[data-source-start]")!.firstChild!;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, Math.min(40, text.textContent!.length));
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await paragraph.dispatchEvent("mouseup");
  await page.getByRole("button", { name: "带来源提问", exact: true }).click();
  await expect(page.locator(".excerpt-card")).toContainText(
    "This synthetic paragraph",
  );
  await page
    .getByLabel("你的问题", { exact: true })
    .fill("解释这一段，并给出来源。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "[S1] 查看来源", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "[S99] 查看来源", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "[S1] 查看来源", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "[S1] 查看来源", exact: true })
    .click();
  await expect(page.locator(".pdf-source-highlight").first()).toBeVisible();
  await page.getByRole("button", { name: "返回结构内容", exact: true }).click();
  await page.getByRole("button", { name: "收起问答", exact: true }).click();
  for (const width of [1920, 1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    for (const theme of ["light", "dark"] as const) {
      if ((await page.locator("html").getAttribute("data-theme")) !== theme)
        await page.getByLabel("切换浅深主题").click();
      await expect(
        page.getByRole("button", { name: "生成翻译", exact: true }),
      ).toHaveCSS(
        "background-color",
        theme === "dark" ? "rgb(29, 30, 35)" : "rgb(255, 255, 255)",
      );
      await page
        .locator(".structure-scroll")
        .evaluate((el) => (el.scrollTop = 0));
      await expect(page.locator(".structure-block").first()).toBeVisible();
      await page.screenshot({
        path: `../.devnotes/dual-translation-structure-${width}-${theme}.png`,
        animations: "disabled",
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  // Explicit local mode without a highlighted quote uses verified visible context.
  await page.getByRole("button", { name: "论文问答", exact: true }).click();
  await page
    .getByRole("button", { name: "选区／当前段落", exact: true })
    .click();
  await page
    .getByLabel("你的问题", { exact: true })
    .fill("解释当前内容并给出来源。");
  const submitted = page.waitForRequest(
    (r) => r.url().endsWith("/api/paper/chat") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect((await submitted).postDataJSON().source_ids.length).toBeGreaterThan(0);
  await expect(
    page.getByRole("button", { name: "[S1] 查看来源", exact: true }).last(),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起问答", exact: true }).click();
  const originalResult = new URL(page.url()).searchParams.get("result");
  let unexpectedJobs = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/processing/jobs"))
      unexpectedJobs++;
  });
  await page.getByLabel("阅读内容").selectOption("original");
  await expect(
    page.locator('.pdf-page[data-rendered="true"]').first(),
  ).toBeVisible();
  await page.getByLabel("阅读内容").selectOption("structure");
  await expect(page.locator(".structure-block").first()).toBeVisible();
  expect(new URL(page.url()).searchParams.get("result")).toBe(originalResult);
  expect(unexpectedJobs).toBe(0);
  const other = await page.context().newPage();
  await other.goto(page.url());
  await expect(other.locator(".structure-block").first()).toBeVisible();
  await page.getByLabel("退出登录", { exact: true }).click();
  for (const tab of [page, other]) {
    await expect(
      tab.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    await expect(tab.locator(".structure-block, canvas, .message")).toHaveCount(
      0,
    );
  }
  await other.close();
  expect(errors).toEqual([]);
  expect(csp).toEqual([]);
  expect(external).toEqual([]);
});
