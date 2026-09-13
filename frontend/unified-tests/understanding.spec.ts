import { test, expect } from "@playwright/test";

test("single paper overview, interpretation, source return, QA and offline exports", async ({
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
      !r.url().startsWith("data:") &&
      !r.url().startsWith("blob:")
    )
      external.push(r.url());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=c-4&view=analysis");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  const csrf = (await page.context().cookies()).find(
    (c) => c.name === "paperpilot_csrf",
  )!.value;
  const headers = { "X-CSRF-Token": csrf };
  const preflight = await page.request.post(
    "/api/paper/c-4/processing/preview",
    { headers, data: {} },
  );
  expect(preflight.ok()).toBeTruthy();
  const created = await page.request.post("/api/paper/c-4/processing/jobs", {
    headers,
    data: { kind: "parse", preflightId: (await preflight.json()).preflightId },
  });
  const job = (await created.json()).job;
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/processing/jobs/${job.id}`)
          ).json()
        ).job.status,
    )
    .toBe("completed");
  await page.goto("/?paper=c-4&view=analysis");
  await expect(
    page.getByText("完整解析原文 · 2 页", { exact: true }),
  ).toBeVisible();
  async function generate(name: string) {
    await page
      .locator(".understanding-actions")
      .getByRole("button", { name: `生成${name}`, exact: true })
      .click();
    await page
      .getByRole("button", { name: "核对处理量与预算", exact: true })
      .click();
    await expect(page.getByText(/模型：fixture/)).toBeVisible();
    await page.getByRole("button", { name: "确认生成", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "已完成", exact: true }),
    ).toBeVisible({ timeout: 60000 });
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await expect(page.locator(".understanding-scroll .markdown")).toContainText(
      "合成验证分析",
    );
  }
  await generate("AI 概览");
  await expect(page.locator(".understanding-scroll math")).toBeVisible();
  await page
    .getByRole("navigation", { name: "论文工作区" })
    .getByRole("button", { name: "深度解读", exact: true })
    .click();
  await generate("深度解读");
  await page.locator(".understanding-scroll .evidence-link").first().click();
  await expect(
    page.locator('.pdf-page[data-rendered="true"]').first(),
  ).toBeVisible();
  await expect(page.locator(".textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await expect(page.locator(".pdf-source-highlight").first()).toBeVisible();
  await page.getByRole("button", { name: "返回深度解读", exact: true }).click();
  await expect(page.locator(".understanding-scroll .markdown")).toContainText(
    "合成验证分析",
  );
  await page
    .getByLabel("你的问题", { exact: true })
    .fill("受控实验报告了什么？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("已读取服务端保存的历史。", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".message-scope")).toContainText("整篇论文问答");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "含图片包", exact: true }).click();
  const downloaded = await downloadPromise;
  expect(downloaded.suggestedFilename()).toContain("iPaper-");
  await downloaded.saveAs("../.devnotes/single-paper-browser/analysis.zip");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  await page.locator(".understanding-scroll").evaluate((e) => {
    e.scrollTop = e.scrollHeight * 0.65;
  });
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get("/api/paper/c-4/understanding-position")
          ).json()
        ).offset || 0,
    )
    .toBeGreaterThan(0.5);
  const sessionId = await page
    .getByLabel("聊天会话", { exact: true })
    .inputValue();
  expect(sessionId).not.toBe("");
  await page.reload();
  await expect(page.locator(".understanding-scroll .markdown")).toContainText(
    "合成验证分析",
  );
  await expect
    .poll(() =>
      page
        .locator(".understanding-scroll")
        .evaluate(
          (e) => e.scrollTop / Math.max(1, e.scrollHeight - e.clientHeight),
        ),
    )
    .toBeGreaterThan(0.5);
  await expect(page.getByLabel("聊天会话", { exact: true })).toHaveValue(
    sessionId,
  );
  await expect(page.locator(".message.assistant")).toContainText(
    "合成来源回答",
  );
  await page.evaluate(() => {
    window.print = () => {
      document.documentElement.dataset.printInvoked = "yes";
    };
  });
  await page.getByRole("button", { name: "浏览器打印", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-print-invoked",
    "yes",
  );
  await page.pdf({
    path: "../.devnotes/single-paper-browser/analysis-print.pdf",
    preferCSSPageSize: true,
    printBackground: true,
  });
  await page.evaluate(() => dispatchEvent(new Event("afterprint")));
  await page.locator(".understanding-scroll").evaluate((e) => {
    e.scrollTop = 0;
  });
  for (const width of [1920, 1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(
        (t) => (document.documentElement.dataset.theme = t),
        theme,
      );
      await page.waitForTimeout(200);
      await page.screenshot({
        path: `../.devnotes/single-paper-browser/analysis-${width}-${theme}.png`,
      });
      expect(
        await page.locator("body").evaluate((e) => e.scrollWidth <= innerWidth),
      ).toBeTruthy();
    }
  }
  await page.getByRole("button", { name: "切换论文问答", exact: true }).click();
  await expect(page.getByLabel("你的问题", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回分析", exact: true }).click();
  await expect(page.locator(".understanding-scroll")).toBeVisible();
  await page.locator(".analysis-evidence summary").click();
  await page
    .locator(".analysis-evidence")
    .getByRole("button", { name: "据此提问", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("你的问题", { exact: true })).toBeVisible();
  await page
    .getByLabel("你的问题", { exact: true })
    .fill("选中的来源说明了什么？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("已读取服务端保存的历史。", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".message-scope").last()).toContainText("局部问答");
  const jobs = (await (await page.request.get("/api/processing/jobs")).json())
    .jobs;
  expect(jobs.filter((j: any) => j.kind === "parse")).toHaveLength(1);
  expect(errors).toEqual([]);
  expect(csp).toEqual([]);
  expect(external).toEqual([]);
});
