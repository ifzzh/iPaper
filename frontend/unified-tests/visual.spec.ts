import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Design-system acceptance for the UI phase 1: the primary/brand colour must
// not be green, the library must be denser, and the shell must not overflow.
const legacyGreen = [
  "#128b80",
  "#49b6a2",
  "#67cdb8",
  "#1eaa8e",
  "#138568",
  "#087366",
  "#16b98c",
];

test("desktop and mobile unified visual acceptance with bounded PDF rendering", async ({
  page,
}) => {
  test.setTimeout(120000);
  const root = resolve("../.devnotes/ui-phase1-acceptance/synthetic");
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const errors: string[] = [],
    violations: string[] = [],
    external: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.2:7191") &&
      !r.url().startsWith("blob:") &&
      !r.url().startsWith("data:")
    )
      external.push(r.url());
  });
  await page.exposeFunction("recordViolation", (v: string) =>
    violations.push(v),
  );
  await page.addInitScript(() =>
    addEventListener("securitypolicyviolation", (e) =>
      (window as any).recordViolation(e.effectiveDirective),
    ),
  );
  await page.request.post("/api/auth/login", {
    data: { username: "reader_one", password: "workbench-test-pass" },
  });
  const measurements: any[] = [];
  for (const width of [1920, 1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    for (const theme of ["light", "dark"]) {
      await page.goto("/?paper=a-0");
      await expect(page.locator(".paper-row")).toHaveCount(50);
      while ((await page.locator("html").getAttribute("data-theme")) !== theme)
        await page.getByLabel("切换浅深主题").click();
      await page.screenshot({
        path: resolve(root, `library-${width}-${theme}.png`),
      });
      const start = Date.now();
      await page.goto("/?view=reader&paper=a-0&document=original");
      await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
        "Synthetic reader validation",
      );
      await expect(page.locator(".page-count")).toHaveText("/ 100");
      const metrics = await page.locator(".pdf-panel").evaluate((el) => ({
        height: el.clientHeight,
        canvasCount: el.querySelectorAll("canvas").length,
        text: el.querySelector(".textLayer")?.textContent?.slice(0, 80),
      }));
      measurements.push({
        width,
        theme,
        firstRenderMs: Date.now() - start,
        ...metrics,
      });
      expect(metrics.height).toBeGreaterThan(width === 390 ? 400 : 600);
      expect(metrics.canvasCount).toBeLessThan(12);
      await page.screenshot({
        path: resolve(root, `reader-${width}-${theme}.png`),
      });
      if (width === 390) {
        await page
          .getByRole("button", { name: "论文问答", exact: true })
          .click();
        await expect(page.getByLabel("你的问题")).toBeVisible();
        await page.screenshot({
          path: resolve(root, `chat-${width}-${theme}.png`),
        });
        await page
          .getByRole("button", { name: "返回阅读", exact: true })
          .click();
      }
    }
  }

  // --- Design tokens: the brand/primary surfaces must not be green. ---
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=a-0");
  await expect(page.locator(".detail-title")).toBeVisible();
  await expect(page.locator(".paper-row")).toHaveCount(50);
  const audit = await page.evaluate(() => {
    const isGreenish = (rgb: string) => {
      const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/.exec(
        rgb || "",
      );
      if (!m) return false;
      const [r, g, b] = [+m[1], +m[2], +m[3]];
      const a = m[4] === undefined ? 1 : +m[4];
      if (a < 0.2) return false;
      return g > r + 10 && g > b + 6;
    };
    const roles = [
      ["brand mark", ".brand-mark"],
      ["primary button", "button.primary, .button.primary"],
      ["active sidebar nav", ".sidebar-nav button.active"],
      ["active scope tab", ".scope-tabs button.active"],
      ["active paper tab", ".paper-tab.active"],
      ["selected paper row", ".paper-row.selected"],
      ["success badge", ".badge.success"],
      ["keyword chip", ".keyword-chip"],
      ["filter chip", ".filter-chip"],
    ];
    const out: any[] = [];
    for (const [label, selector] of roles) {
      const el = document.querySelector(selector);
      if (!el) continue;
      const s = getComputedStyle(el);
      out.push({
        label,
        selector,
        color: s.color,
        background: s.backgroundColor,
        border: s.borderColor,
        greenPrimary:
          isGreenish(s.background) ||
          isGreenish(s.color) ||
          isGreenish(s.borderColor),
      });
    }
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim();
    return { roles: out, accent };
  });
  for (const role of audit.roles)
    expect(
      { role: role.label, green: role.greenPrimary },
      `primary surface "${role.label}" resolved to a green colour`,
    ).toEqual({ role: role.label, green: false });
  for (const legacy of legacyGreen)
    expect(audit.accent.toLowerCase()).not.toBe(legacy);

  // --- Library density, measured where rows intersect the list viewport. ---
  const rows = await page.locator(".paper-row").count();
  const density = await page.evaluate(() => {
    const list = document.querySelector(".paper-list");
    if (!list) return null;
    const viewport = list.getBoundingClientRect();
    let full = 0;
    let partial = 0;
    let firstHeight = null;
    for (const row of list.querySelectorAll(".paper-row")) {
      const rect = row.getBoundingClientRect();
      const overlap =
        Math.min(rect.bottom, viewport.bottom) - Math.max(rect.top, viewport.top);
      if (overlap <= 0) continue;
      if (firstHeight === null && rect.height) firstHeight = Math.round(rect.height);
      if (rect.top >= viewport.top - 1 && rect.bottom <= viewport.bottom + 1)
        full += 1;
      else partial += 1;
    }
    return { full, partial, firstHeight, viewportHeight: Math.round(viewport.height) };
  });
  expect(rows).toBeGreaterThanOrEqual(50);
  expect(density!.full).toBeGreaterThanOrEqual(4);
  await page.goto("/");
  await expect(page.locator(".paper-row").first()).toBeVisible();
  expect(await page.locator(".paper-details:visible").count()).toBe(0);

  // Expanding is a persisted toggle and the calendar needs its data: retry the
  // click a bounded number of times and wait for the grid, not the legend.
  const openActivity = async () => {
    await expect(page.locator(".library-activity-toggle")).toBeVisible({ timeout: 20000 });
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (await page.locator(".reading-activity-scroll").isVisible().catch(() => false)) return;
      await page.locator(".library-activity-toggle").click();
      if (await page.locator(".reading-activity-scroll").isVisible().catch(() => false)) return;
      await page.waitForTimeout(600);
    }
    await expect(page.locator(".reading-activity-scroll")).toBeVisible({ timeout: 20000 });
  };

  // --- Reading activity: the calendar fills its column and stays on-screen. ---
  await openActivity();
  await expect(page.locator(".reading-activity .heat-cell").first()).toBeVisible();
  const heat = await page.evaluate(() => {
    const wrap = document.querySelector(".reading-activity-grid-wrap");
    const grid = document.querySelector(".reading-activity-grid");
    const cell = document.querySelector(".reading-activity .heat-cell");
    const card = document.querySelector(".reading-activity");
    const scroll = document.querySelector(".reading-activity-scroll");
    return {
      weeks: Number(getComputedStyle(wrap!).getPropertyValue("--heat-weeks")),
      wrapWidth: wrap!.getBoundingClientRect().width,
      cardWidth: card!.getBoundingClientRect().width,
      gridWidth: grid!.getBoundingClientRect().width,
      cellWidth: cell!.getBoundingClientRect().width,
      scrollable: scroll!.scrollWidth > scroll!.clientWidth + 1,
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(heat.weeks).toBeGreaterThanOrEqual(52);
  expect(heat.cellWidth).toBeGreaterThanOrEqual(9);
  // Fills (or scrolls within) the card without spilling off the page.
  expect(heat.wrapWidth).toBeLessThanOrEqual(heat.cardWidth + 1);
  expect(heat.wrapWidth).toBeGreaterThan(heat.cardWidth * 0.6);
  expect(heat.pageOverflow).toBeLessThanOrEqual(1);

  // --- Mobile: a year of squares scrolls inside the component only. ---
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await openActivity();
  await expect(page.locator(".reading-activity .heat-cell").first()).toBeVisible();
  const mobileHeat = await page.evaluate(() => {
    const cell = document.querySelector(".reading-activity .heat-cell");
    const scroll = document.querySelector(".reading-activity-scroll");
    return {
      cellWidth: cell!.getBoundingClientRect().width,
      scrollable: scroll!.scrollWidth > scroll!.clientWidth + 1,
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(mobileHeat.cellWidth).toBeGreaterThanOrEqual(9);
  expect(mobileHeat.scrollable).toBe(true);
  expect(mobileHeat.pageOverflow).toBeLessThanOrEqual(1);


  // --- Intermediate width and no horizontal overflow in either theme. ---
  const widths: any[] = [];
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const theme of ["light", "dark"] as const) {
      await page.goto("/?paper=a-0");
      await expect
        .poll(
          async () =>
            page.locator(".paper-row:visible, .detail-title:visible").count(),
        )
        .toBeGreaterThan(0);
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      await page.screenshot({
        path: resolve(root, `library-${width}-${theme}.png`),
        animations: "disabled",
      });
      widths.push({
        width,
        theme,
        overflow: await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        ),
      });
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=reader&paper=a-0&document=original");
  await expect(page.locator(".page-count")).toHaveText("/ 100");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    ),
  ).toBeLessThanOrEqual(1);
  for (const row of widths) expect(row.overflow).toBeLessThanOrEqual(1);

  writeFileSync(
    resolve(root, "synthetic-metrics.json"),
    JSON.stringify(
      {
        measurements,
        audit,
        rows,
        density,
        widths,
        errors,
        violations,
        external,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  expect(errors).toEqual([]);
  expect(violations).toEqual([]);
  expect(external).toEqual([]);
});