import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("reader_one");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".paper-row")).toHaveCount(50);
}

test("unified login, cold library, categories, preferences and feature pages", async ({
  page,
}) => {
  await login(page);
  await page.getByLabel("搜索文献").fill("Learning to read");
  await expect(page.locator(".paper-row")).toHaveCount(1);
  await page.locator(".paper-row").click();
  await expect(page.locator(".paper-details")).toContainText(
    "Learning to read",
  );
  await page.getByLabel("新建分类", { exact: true }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("隔离验收分类");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建", exact: true })
    .click();
  await expect(page.locator(".category-tree")).toContainText("隔离验收分类");
  for (const label of ["Daily arXiv", "任务中心", "设置"]) {
    await page
      .getByRole("navigation")
      .getByRole("button", { name: label, exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: label, exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    if (label === "Daily arXiv") {
      await expect(page.locator(".daily-card")).toContainText("Daily 合成验收");
      await page
        .getByRole("button", { name: "加入并阅读", exact: true })
        .click();
      await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
        "Synthetic reader validation",
      );
      await expect(page.locator(".page-count")).toHaveText("/ 2");
      expect(
        (await (await page.request.get("/api/reading-list")).json()).length,
      ).toBeGreaterThan(0);
      await page
        .getByLabel("关闭论文标签 Daily 合成验收：从发现到阅读", {
          exact: true,
        })
        .click();
    }
  }
  await page.getByRole("button", { name: "个人账号", exact: true }).click();
  await page.getByLabel("显示名称").fill("合成验收账号");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("设置已保存。", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "个人账号", exact: true }).click();
  await expect(page.getByLabel("显示名称")).toHaveValue("合成验收账号");
});

test("continuous PDF, strict CSP, positions, translated variant and selection chat", async ({
  page,
}) => {
  const violations: string[] = [];
  await page.exposeFunction("captureCsp", (v: string) => violations.push(v));
  await page.addInitScript(() =>
    addEventListener("securitypolicyviolation", (e) =>
      (window as any).captureCsp(e.effectiveDirective),
    ),
  );
  await page.request.post("/api/auth/login", {
    data: { username: "reader_one", password: "workbench-test-pass" },
  });
  await page.goto("/?view=reader&paper=a-0");
  const text = page.locator(".pdf-panel .textLayer");
  await expect(text.first()).toContainText("Synthetic reader validation");
  await expect(page.locator(".page-count")).toHaveText("/ 100");
  // Other papers may already be restored from this user's saved workspace.
  await expect(
    page.getByRole("tab", {
      name: "Learning to read the world: a unified framework for embodied reasoning",
      exact: true,
    }),
  ).toHaveCount(1);
  await page.getByLabel("页码", { exact: true }).fill("5");
  await page.getByLabel("页码", { exact: true }).press("Enter");
  await expect(
    page.locator('.pdf-panel [data-page="5"] .textLayer'),
  ).toContainText("page 5");
  await page.getByLabel("缩放", { exact: true }).selectOption("0.75");
  await expect
    .poll(async () =>
      (await page.request.get("/api/paper/a-0/reading-position")).json(),
    )
    .toMatchObject({ original: { page: 5, zoom: 0.75 } });
  await page.reload();
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("5");
  await expect(page.getByLabel("缩放", { exact: true })).toHaveValue("0.75");
  await page.getByLabel("阅读内容").selectOption("translated");
  await expect(page.locator(".page-count")).toHaveText("/ 2");
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("1");
  await expect(text.first()).toContainText("中文");
  await text
    .first()
    .locator("span")
    .first()
    .evaluate((el) => {
      const r = document.createRange();
      r.selectNodeContents(el);
      const s = getSelection()!;
      s.removeAllRanges();
      s.addRange(r);
    });
  await page.getByRole("button", { name: "选区提问", exact: true }).click();
  await expect(page.locator(".excerpt-card")).toContainText("第 1 页");
  await page.getByLabel("你的问题").fill("这段文字说明了什么？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "合成来源回答",
  );
  await expect(page.locator(".message img")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "合成来源回答",
  );
  expect(violations).toEqual([]);
});

test("password stays in memory, malformed and missing PDFs have distinct errors", async ({
  page,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_one", password: "workbench-test-pass" },
  });
  await page.goto("/?view=reader&paper=a-1");
  await page.getByLabel("文档密码", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "打开 PDF", exact: true }).click();
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await page.goto("/?view=reader&paper=a-2");
  await expect(page.getByRole("alert")).toContainText("PDF 内容无法解析");
  await page.goto("/?view=reader&paper=a-3");
  await expect(page.getByRole("alert")).toContainText("PDF 文件不存在");
});
