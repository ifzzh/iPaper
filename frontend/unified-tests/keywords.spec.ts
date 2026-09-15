import { test, expect } from "@playwright/test";

test("local extraction, correction, filtering, merge undo and responsive library", async ({
  page,
}, info) => {
  const faults: string[] = [],
    external: string[] = [],
    csp: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.2:7191/") &&
      !/^(blob:|data:)/.test(r.url())
    )
      external.push(r.url());
  });
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/.test(m.text())) csp.push(m.text());
  });
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".paper-row")).toHaveCount(6);
  await page.locator(".paper-row").first().click();
  await expect(page.locator(".keyword-section")).toBeVisible();
  await page.getByLabel("编辑论文标签").click();
  await page.getByLabel("新标签名称").fill("组会候选");
  await page.getByLabel("添加论文标签").click();
  await expect(
    page
      .locator(".keyword-section")
      .getByRole("button", { name: "组会候选", exact: true }),
  ).toBeVisible();
  const pid = new URL(page.url()).searchParams.get("paper")!;
  const baseline = await (await page.request.get("/api/paper/" + pid)).json();
  await page
    .locator(".keyword-section")
    .getByRole("button", { name: "组会候选", exact: true })
    .click();
  await expect(page.locator(".paper-row")).toHaveCount(1);
  await page.getByRole("button", { name: "清除标签", exact: true }).click();
  await expect(page.locator(".paper-row")).toHaveCount(6);
  await page
    .locator(".keyword-section")
    .getByRole("button", { name: "整理标签", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("不请求书目网站");
  await page.getByRole("button", { name: "开始整理", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "任务中心", exact: true }),
  ).toBeVisible();
  await page.goto("/?paper=" + pid);
  await expect(page.locator(".keyword-section")).toContainText("组会候选");
  await expect
    .poll(async () => {
      const x = await (
        await page.request.get("/api/paper/" + pid + "/tags")
      ).json();
      return x.tags.length;
    })
    .toBeGreaterThan(1);
  await page.getByLabel("编辑论文标签").click();
  const current = await (
    await page.request.get("/api/paper/" + pid + "/tags")
  ).json();
  const automatic = current.tags.find((t: any) => !t.manual);
  expect(automatic).toBeTruthy();
  await page
    .getByRole("button", { name: "从本篇移除 " + automatic.name, exact: true })
    .click();
  await page.reload();
  expect(
    (
      await (await page.request.get("/api/paper/" + pid + "/tags")).json()
    ).tags.some((t: any) => t.id === automatic.id),
  ).toBe(false);
  await page.getByRole("button", { name: "管理标签", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /组会候选/ })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("标签名称", { exact: true })
    .fill("近期组会");
  await page.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(page.locator(".tag-catalog")).toContainText("近期组会");
  await page.getByRole("dialog").getByLabel("关闭", { exact: true }).click();
  await expect(page.locator(".keyword-section")).toContainText("近期组会");
  for (const width of [1920, 1440, 390])
    for (const theme of ["light", "dark"]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await page.evaluate(
        (t) => (document.documentElement.dataset.theme = t),
        theme,
      );
      await page.locator(".keyword-section").scrollIntoViewIfNeeded();
      await page.screenshot({
        path: info.outputPath(`${width}-${theme}-keywords.png`),
        fullPage: true,
        animations: "disabled",
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
    }
  const after = await (await page.request.get("/api/paper/" + pid)).json();
  for (const key of ["id", "file_path", "filename", "abstract"])
    expect(after[key]).toEqual(baseline[key]);
  expect(faults).toEqual([]);
  expect(external).toEqual([]);
  expect(csp).toEqual([]);
});

test("explicit simulated model enhancement is reused without a second request", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator(".paper-row").last().click();
  const target = page.url();
  for (let i = 0; i < 2; i++) {
    await page
      .locator(".keyword-section")
      .getByRole("button", { name: "整理标签", exact: true })
      .click();
    await page.getByLabel("提取方式").selectOption("model");
    await expect(page.getByRole("dialog")).toContainText("synthetic-keywords");
    await expect(page.getByRole("dialog")).toContainText(
      `最多 ${i === 0 ? 1 : 0} 次请求`,
    );
    await page.getByRole("button", { name: "开始整理", exact: true }).click();
    await page.goto(target);
    await expect(page.locator(".keyword-section")).toContainText(
      "合成标签验收",
    );
  }
  expect(
    (await (await page.request.get("/test-keyword-calls")).json())
      .modelRequests,
  ).toBe(1);
});

test("server filters and fixed cross-page selection agree beyond one hundred papers", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("reader_one");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".paper-row")).toHaveCount(50);
  const headers = {
    "X-CSRF-Token": (await page.context().cookies()).find(
      (c) => c.name === "paperpilot_csrf",
    )!.value,
  };
  const ids = Array.from({ length: 250 }, (_, i) => "a-" + i);
  for (const [name, paperIds] of [
    ["大样本", ids],
    ["子样本", ids.slice(0, 150)],
  ] as const) {
    const preview = await (
      await page.request.post("/api/keywords/preview", {
        headers,
        data: { paperIds },
      })
    ).json();
    expect(
      (
        await page.request.post("/api/tags/batch", {
          headers,
          data: { paperIds, action: "add", name, revisions: preview.revisions },
        })
      ).ok(),
    ).toBe(true);
  }
  await page.reload();
  const tags = (await (await page.request.get("/api/tags")).json()).tags;
  await page
    .getByLabel("按关键词筛选")
    .selectOption(tags.find((t: any) => t.name === "大样本").id);
  await page
    .getByLabel("按关键词筛选")
    .selectOption(tags.find((t: any) => t.name === "子样本").id);
  await expect(page.locator(".pagination")).toContainText("/ 150");
  await expect(page.locator(".paper-row")).toHaveCount(50);
  await page.getByLabel("下一页列表").click();
  await expect(page.locator(".pagination")).toContainText("51–100 / 150");
  await page.getByLabel("多标签匹配方式").selectOption("any");
  await expect(page.locator(".pagination")).toContainText("/ 250");
  await page.getByRole("button", { name: "选择当前页", exact: true }).click();
  await page
    .getByRole("button", { name: "选择全部匹配（250 篇）", exact: true })
    .click();
  await expect(page.locator(".batch-toolbar")).toContainText("已选择 250 篇");
  await page
    .getByRole("button", { name: "整理／编辑标签", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("已固定 250 篇论文");
  await page.getByRole("combobox", { name: "操作", exact: true }).selectOption("add");
  await page.getByLabel("标签名称", { exact: true }).fill("跨页批量");
  await page
    .getByRole("button", { name: "应用到所选论文", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("撤销");
  const after = (await (await page.request.get("/api/tags")).json()).tags;
  expect(after.find((t: any) => t.name === "跨页批量").count).toBe(250);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true }).first()
    .click();
  // Reducing the selection to the current page must discard the earlier snapshot.
  await page.getByRole("button", { name: "选择当前页", exact: true }).click();
  await page
    .getByRole("button", { name: "整理／编辑标签", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("已固定 50 篇论文");
});
