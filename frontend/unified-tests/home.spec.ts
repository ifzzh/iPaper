import { test, expect, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";

const evidence = resolve("../.devnotes/home-dashboard/isolated");

async function capture(page: Page, filename: string) {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const actual = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    background: getComputedStyle(document.querySelector(".app-content")!).backgroundColor,
    viewport: { width: innerWidth, height: innerHeight },
  }));
  expect(actual.colorScheme).toBe(actual.theme);
  const expectedTheme = filename.includes("-dark") ? "dark" : "light";
  expect(actual.theme).toBe(expectedTheme);
  await page.screenshot({ path: resolve(evidence, filename), animations: "disabled" });
  writeFileSync(resolve(evidence, filename.replace(/\.png$/, ".json")), JSON.stringify({
    ...actual,
    capturedAt: new Date().toISOString(),
    scenario: filename,
    environment: "isolated synthetic unified application",
    versionBase: JSON.parse(readFileSync("package.json", "utf8")).version,
    baseCommit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
    source: "working tree; not a release image",
    imageDigest: null,
    buildManifestSha256: createHash("sha256").update(readFileSync("../static/workbench/.vite/manifest.json")).digest("hex"),
  }, null, 2));
}

async function login(page: Page, username = "home_reader") {
  const result = await page.request.post("/api/auth/login", {
    data: { username, password: "workbench-test-pass" },
  });
  expect(result.ok()).toBeTruthy();
  return {
    "X-CSRF-Token": (await page.context().cookies()).find(
      (c) => c.name === "paperpilot_csrf",
    )!.value,
  };
}

test("independent home uses real scoped APIs, resumes reading, and gives the library its space back", async ({
  page,
}) => {
  test.setTimeout(90000);
  const headers = await login(page);
  const errors: string[] = [];
  const external: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (request) => {
    if (!/^(http:\/\/127\.0\.0\.2:7191|blob:|data:)/.test(request.url()))
      external.push(request.url());
  });
  // This is the temporary unified-server database, with its synthetic PDFs.
  await page.request.put("/api/workspace/state", {
    headers,
    data: {
      tabs: ["h-0", "h-1", "h-2"],
      activePaper: "h-0",
      theme: "light",
      readingActivityOpen: true,
    },
  });
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const history: Record<string, number> = {};
  for (let i = 0; i < 35; i++) {
    const day = new Date(`${today}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - i);
    history[day.toISOString().slice(0, 10)] = [12, 26, 9, 42, 0, 18, 33][i % 7];
  }
  expect(
    (
      await page.request.post("/api/settings/reading-history", {
        headers,
        data: history,
      })
    ).ok(),
  ).toBeTruthy();
  const topicResponse = await page.request.post("/api/topics", {
    headers,
    data: { name: "具身智能" },
  });
  expect(topicResponse.ok()).toBeTruthy();
  const topic = await topicResponse.json();
  for (const id of ["h-0", "h-1", "h-2"]) {
    const before = await (
      await page.request.get(`/api/paper/${id}/topics`)
    ).json();
    expect(
      (
        await page.request.patch(`/api/paper/${id}/topics`, {
          headers,
          data: { action: "add", revision: before.revision, topicId: topic.id },
        })
      ).ok(),
    ).toBeTruthy();
  }
  for (const [name, ids] of [
    ["智能体与记忆", ["h-0", "h-1", "h-3", "h-4", "h-5"]],
    ["计算机视觉", ["h-5", "h-6", "h-7", "h-8"]],
    ["计算系统", ["h-2", "h-9"]],
  ] as const) {
    const node = await (
      await page.request.post("/api/topics", { headers, data: { name } })
    ).json();
    for (const id of ids) {
      const before = await (
        await page.request.get(`/api/paper/${id}/topics`)
      ).json();
      expect(
        (
          await page.request.patch(`/api/paper/${id}/topics`, {
            headers,
            data: {
              action: "add",
              revision: before.revision,
              topicId: node.id,
            },
          })
        ).ok(),
      ).toBeTruthy();
    }
  }
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "研究工作台." }),
  ).toBeVisible();
  await expect(page.locator(".home-stat.collection strong")).toHaveText("11");
  await expect(page.locator(".home-resume-feature")).toContainText(
    "AutoSci: A Memory-Centric",
  );
  await expect(page.locator(".home-chart-bars button")).toHaveCount(7);
  await expect(page.locator(".reading-activity-column")).toHaveCount(53);
  await expect(
    page.locator(".home-topic").filter({ hasText: "具身智能" }),
  ).toBeVisible();
  const data = await (
    await page.request.get("/api/settings/reading-activity?weeks=53")
  ).json();
  await expect(page.locator(".home-stat.minutes strong")).toHaveText(
    String(Math.round(data.summary.weekMinutes)),
  );
  await page.getByRole("button", { name: "30 天", exact: true }).click();
  await expect(page.locator(".home-chart-bars button")).toHaveCount(30);
  await page.getByRole("button", { name: "7 天", exact: true }).click();
  await page.getByRole("button", { name: "近 12 周", exact: true }).click();
  await expect(page.locator(".reading-activity-column")).toHaveCount(12);
  await page.getByRole("button", { name: "近一年", exact: true }).click();

  mkdirSync(evidence, { recursive: true });
  const measurements = [];
  for (const width of [1920, 1440, 1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    if (width === 390)
      await expect
        .poll(() =>
          page
            .locator(".app-sidebar")
            .evaluate((el) => el.getBoundingClientRect().right),
        )
        .toBeLessThanOrEqual(0);
    await page.locator(".home-page").evaluate((el) => {
      el.scrollTop = 0;
    });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const measurement = await page
      .locator(".home-page")
      .evaluate((el) => ({
        width: el.clientWidth,
        scrollWidth: el.scrollWidth,
        background: getComputedStyle(el).backgroundColor,
        height: el.clientHeight,
        scrollHeight: el.scrollHeight,
      }));
    expect(measurement.scrollWidth).toBeLessThanOrEqual(measurement.width + 1);
    measurements.push({
      viewport: width,
      theme: "light",
      synthetic: true,
      ...measurement,
    });
    await capture(page, `home-${width}-light.png`);
    await page.locator(".home-bottom").scrollIntoViewIfNeeded();
    await capture(page, `home-${width}-bottom-light.png`);
  }
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.locator(".heat-cell.level-0:not(.future)").first().click();
  await expect(page.locator(".reading-activity-detail")).toContainText("这一天还没有阅读记录");
  await page.locator(".heat-cell.today").click();
  await expect(page.locator(".reading-activity-detail")).toContainText("这一天有累计时长");
  await page.getByLabel("关闭日期详情").click();
  await page.getByLabel("切换浅深主题").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator(".home-page").evaluate((el) => {
    el.scrollTop = 0;
  });
  await capture(page, "home-1440-dark.png");
  await page.getByLabel("切换浅深主题").click();
  await page.getByRole("button", { name: "继续阅读", exact: true }).click();
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await page.getByLabel("页码", { exact: true }).fill("5");
  await page.getByLabel("页码", { exact: true }).press("Enter");
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get("/api/paper/h-0/reading-position")
          ).json()
        ).original?.page,
    )
    .toBe(5);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "首页", exact: true })
    .click();
  await expect(page.locator(".home-resume-feature")).toContainText(
    "上次读至第 5 页",
  );
  await page.reload();
  await expect(page.locator(".home-resume-feature")).toContainText(
    "上次读至第 5 页",
  );
  await page.getByRole("button", { name: "继续阅读", exact: true }).click();
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("5");
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "首页", exact: true })
    .click();
  await page.locator(".home-topic").filter({ hasText: "具身智能" }).click();
  await expect(page.locator(".paper-row")).toHaveCount(3);
  await expect(
    page.locator(".library-activity, .reading-activity"),
  ).toHaveCount(0);
  await page.goBack();
  await expect(page.locator(".home-page")).toBeVisible();
  await page.getByRole("button", { name: "查看文献库", exact: true }).click();
  await expect(page.locator(".paper-row")).toHaveCount(11);
  const listHeight = await page
    .locator(".paper-list")
    .evaluate((el) => el.clientHeight);
  expect(listHeight).toBeGreaterThan(650);
  await capture(page, "library-1440-light.png");
  await page.goto("/?paper=h-0");
  await expect(page.locator(".paper-details")).toContainText(
    "AutoSci: A Memory-Centric",
  );
  await page.goto("/");
  await page.getByRole("button", { name: "探索 Daily", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Daily arXiv", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
  writeFileSync(
    resolve(evidence, "measurements.json"),
    JSON.stringify(
      {
        source: "isolated unified fixture; no production state",
        base: "1.9.4",
        measurements,
        listHeight,
        errors,
        external,
      },
      null,
      2,
    ),
  );
});

test("home keeps missing statistics honest and never turns a GET into reading time", async ({
  page,
}) => {
  await login(page, "home_reader");
  const historyBefore = await (
    await page.request.get("/api/settings/reading-history")
  ).json();
  await page.route("**/api/settings/reading-activity?*", (route) =>
    route.fulfill({ status: 503, json: { error: "unavailable" } }),
  );
  await page.goto("/");
  await expect(page.locator(".home-stat.minutes strong")).toHaveText("—");
  await expect(page.locator(".home-trend [role=alert]")).toBeVisible();
  await expect(page.locator(".home-resume-feature")).toBeVisible();
  await page.unroute("**/api/settings/reading-activity?*");
  await page
    .locator(".home-trend")
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(page.locator(".home-chart-bars button")).toHaveCount(7);
  expect(
    await (await page.request.get("/api/settings/reading-history")).json(),
  ).toEqual(historyBefore);
  await page.getByLabel("退出登录", { exact: true }).click();
  await expect(page.locator(".home-page")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
});

test("a new account starts with an honest empty home and an import action", async ({
  page,
}) => {
  await login(page, "home_empty");
  await page.goto("/");
  await expect(page.locator(".home-stat.collection strong")).toHaveText("0");
  await expect(page.locator(".home-empty")).toContainText("导入第一篇文献");
  await page
    .getByRole("button", { name: "导入第一篇文献", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
});
