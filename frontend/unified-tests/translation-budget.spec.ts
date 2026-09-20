import { test, expect } from "@playwright/test";

// The structured-translation budget form: defaults and ceilings come from the
// API, an over-scope names the exact dimension, and the confirmed envelope is
// what the job is created with. Synthetic fixture: fake cloud and fake model.
test("structured translation budget is adjustable and names the exact overage", async ({
  page,
}) => {
  const errors: string[] = [];
  const external: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/.test(m.text())) errors.push(m.text());
  });
  page.on("request", (r) => {
    if (!r.url().startsWith("http://127.0.0.2:7191/") && !r.url().startsWith("data:"))
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
  await expect(page.locator('.pdf-page[data-rendered="true"]').first()).toBeVisible();
  await page.getByLabel("阅读内容").selectOption("structure");
  await page.getByRole("button", { name: "生成结构内容", exact: true }).click();
  await expect(page.getByText("翻译模型：fixture")).toBeVisible();

  // Defaults come from the kind-aware API response, not from page constants.
  const budgetInputs = page.locator(".processing-budget-editor input");
  await page.locator(".processing-budget-editor summary").click();
  await expect(budgetInputs.nth(0)).toHaveValue("500");
  await expect(budgetInputs.nth(3)).toHaveValue("4");
  await expect(page.locator(".processing-budget")).toContainText("部署上限 2000 次 / 12 小时");
  // Before any structure exists the page says so instead of inventing a number.
  await expect(page.locator(".processing-budget + p.muted")).toContainText(
    "解析前无法准确计算块数",
  );

  // Produce the structure first (parse only, no model calls), then the estimate
  // for the translation scope becomes reliable.
  await page.getByLabel("仅解析结构，不调用翻译模型").check();
  await page.getByRole("button", { name: "开始解析", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.getByRole("button", { name: "关闭", exact: true }).first().click();
  // The reader is now in structure mode; "生成翻译" reopens the panel with the
  // parse result attached, so the scope estimate is reliable.
  await page.getByRole("button", { name: "生成翻译", exact: true }).click();
  await expect(page.getByText("翻译模型：fixture")).toBeVisible();
  await expect(page.locator(".processing-budget-editor summary")).toBeVisible();
  await page.locator(".processing-budget-editor summary").click();

  // Read how many base requests this scope really needs (the estimate is
  // computed server-side, so wait for it rather than assuming it is ready).
  const estimateParagraph = page.locator(".processing-budget + p.muted");
  await expect
    .poll(
      async () =>
        (await estimateParagraph.innerText()).match(/基础请求 (\d+) 次/)?.[1] ?? "",
      { timeout: 30000 },
    )
    .not.toBe("");
  const required = Number(
    (await estimateParagraph.innerText()).match(/基础请求 (\d+) 次/)![1],
  );
  expect(required).toBeGreaterThan(0);

  // Too small a budget is refused with the exact dimension and requirement.
  await budgetInputs.nth(0).fill("1");
  await page.waitForTimeout(600);
  const overage = page.locator(".notice.error").first();
  await expect(overage).toContainText("模型请求");
  await expect(overage).toContainText(`需要 ${required}`);
  await expect(overage).toContainText("当前额度 1");
  await expect(page.getByRole("button", { name: "开始生成", exact: true })).toBeDisabled();

  // The offered action raises exactly that dimension and unblocks submission.
  await page.getByRole("button", { name: "使用所需额度" }).click();
  await page.waitForTimeout(600);
  await expect(budgetInputs.nth(0)).toHaveValue(String(required));
  await expect(page.locator(".notice.error")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "开始生成", exact: true })).toBeEnabled();

  // Values above the deploy ceiling are rejected inline, not clamped.
  await budgetInputs.nth(0).fill("99999");
  await page.waitForTimeout(400);
  await expect(page.getByText("超过本次部署上限 2,000")).toBeVisible();
  await expect(page.getByRole("button", { name: "开始生成", exact: true })).toBeDisabled();

  // Zero is invalid rather than silently replaced (checked once the ceiling
  // problem above is cleared, since the form reports the first problem found).
  await budgetInputs.nth(0).fill(String(required));
  await page.waitForTimeout(400);
  await budgetInputs.nth(1).fill("0");
  await page.waitForTimeout(400);
  await expect(page.getByText("必须是正整数")).toBeVisible();
  await budgetInputs.nth(1).fill("1000000");
  await page.waitForTimeout(600);

  // The confirmed envelope is what the created job records.
  await page.getByRole("button", { name: "开始生成", exact: true }).click();
  await expect(page.getByText(`已预留 0/${required} 次请求`, { exact: false })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByText("供应商实际用量", { exact: false })).toBeVisible();
  // Let the job finish: one owner may only have one active processing task, and
  // the resume check below needs the queue to itself.
  await expect(page.locator(".processing-task")).toContainText("已完成", {
    timeout: 90000,
  });

  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});

// Top-up continuation through the real UI: a stopped whole-paper translation
// offers "调整额度并继续", the plan comes from the server, and the confirmed
// whole-task total is recorded without resetting what was already used.
test("stopped translation can be continued with a larger whole-task budget", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=c-4&view=reader");
  await page.getByLabel("账号", { exact: true }).fill("reader_pdf");
  await page.getByLabel("密码", { exact: true }).fill("workbench-test-pass");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("navigation").getByRole("button", { name: "任务中心", exact: true }),
  ).toBeVisible();
  await page.getByRole("navigation").getByRole("button", { name: "任务中心", exact: true }).click();
  const detail = page.locator(".processing-task");
  // Several structured jobs can be listed (the first test creates one), so open
  // the one that is actually stopped at its 1/2 envelope.
  await expect(page.locator(".task-list button").first()).toBeVisible({
    timeout: 20000,
  });
  // Find the seeded stopped job through the API, then open that exact task
  // (other structured jobs may exist from the previous test).
  const stopped = (
    (await (await page.request.get("/api/processing/jobs")).json()).jobs as any[]
  ).find((job) => job.status === "partial");
  expect(stopped, "a stopped translation job is seeded for this check").toBeTruthy();
  await page
    .locator(".task-list button", { hasText: String(stopped.id).slice(0, 8) })
    .first()
    .click();
  await expect(detail).toBeVisible();
  await expect
    .poll(async () => (await detail.innerText()).includes("已预留 1/2 次请求"), {
      timeout: 20000,
    })
    .toBe(true);
  // Reserved (pre-charged) usage is shown apart from supplier-reported usage.
  await expect(detail).toContainText("已预留 1/2 次请求");
  await expect(detail).toContainText("供应商实际用量");
  await expect(detail).toContainText("80 输入");

  await detail.getByRole("button", { name: "调整额度并继续未完成部分" }).click();
  const resume = page.locator(".processing-resume-budget");
  await expect(resume).toContainText("已预留 1 次请求");
  const inputs = resume.locator("input");
  // Defaults to the saved envelope, and the ceiling comes from the server.
  await expect(inputs.nth(0)).toHaveValue("2");
  await expect(inputs.nth(3)).toHaveValue("1");
  // Below what was already used is refused in place.
  await inputs.nth(0).fill("0");
  await expect(resume).toContainText("必须是正整数");
  await expect(resume.getByRole("button", { name: "保存额度并继续" })).toBeDisabled();

  // Equal to what is already used is accepted, but it is not an increase, so
  // continuing stays disabled until the envelope actually grows.
  await inputs.nth(0).fill("1");
  await expect(resume.getByRole("button", { name: "保存额度并继续" })).toBeDisabled();

  await inputs.nth(0).fill("40");
  await inputs.nth(3).fill("4");
  await expect(resume.getByRole("button", { name: "保存额度并继续" })).toBeEnabled();
  // Only one active processing task per owner is allowed, so wait for the
  // previous job to leave the queue before continuing this one.
  await expect
    .poll(
      async () => {
        const jobs = (
          (await (await page.request.get("/api/processing/jobs")).json()).jobs as any[]
        );
        return jobs.filter((job) =>
          ["queued", "running", "cancelling"].includes(job.status),
        ).length;
      },
      { timeout: 90000 },
    )
    .toBe(0);
  await resume.getByRole("button", { name: "保存额度并继续" }).click();
  // The job is queued for continuation and records the raised envelope; the
  // already-used requests are preserved rather than reset.
  await expect(detail).toContainText("已预留 1/40 次请求", { timeout: 30000 });
  expect(errors).toEqual([]);
});
