import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Design-system acceptance for the UI phase 1: the primary/brand colour must
// not be green, the library must be denser, and mobile must not overflow.
const legacyGreen = [
  "#128b80",
  "#49b6a2",
  "#67cdb8",
  "#1eaa8e",
  "#138568",
  "#087366",
  "#16b98c",
];

test("ui phase 1 design tokens: non-green primary, library density, responsive shell", async ({
  page,
}) => {
  test.setTimeout(90000);
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
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=a-0");
  await expect(page.locator(".paper-row").first()).toBeVisible();
  await expect(page.locator(".detail-title")).toBeVisible();

  const audit = await page.evaluate(() => {
    const isGreenish = (rgb: string) => {
      const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/.exec(rgb || "");
      if (!m) return false;
      const [r, g, b] = [+m[1], +m[2], +m[3]];
      const a = m[4] === undefined ? 1 : +m[4];
      if (a < 0.2) return false;
      return g > r + 10 && g > b + 6;
    };
    const roles = [
      ["brand mark", ".brand-mark"],
      ["primary button", "button.primary, .button.primary"],
      ["active rail nav", ".app-rail nav button.active"],
      ["active library nav", ".library-navigation button.active"],
      ["active workspace tab", ".workspace-tabs > button.active"],
      ["filter chip", ".filter-chip"],
      ["selected paper row", ".paper-row.selected"],
      ["focus ring", "[tabindex]:focus-visible"],
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
    const root = getComputedStyle(document.documentElement);
    const tokens: Record<string, string> = {};
    for (const name of [
      "--bg",
      "--surface",
      "--subtle",
      "--text",
      "--border",
      "--accent",
      "--accent-bg",
      "--danger",
    ])
      tokens[name] = root.getPropertyValue(name).trim();
    return { roles: out, tokens };
  });

  // The legacy teal palette may only survive as a status colour, never on the
  // primary/brand surfaces listed above.
  for (const role of audit.roles)
    expect(
      { role: role.label, green: role.greenPrimary },
      `primary surface "${role.label}" resolved to a green colour`,
    ).toEqual({ role: role.label, green: false });

  // The brand accent token itself must not be one of the legacy greens.
  const accent = (audit.tokens["--accent"] || "").toLowerCase();
  for (const legacy of legacyGreen) expect(accent).not.toBe(legacy);
  expect(accent.length).toBeGreaterThan(0);

  // Library density: a 1440 desktop must show a useful number of rows.
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?paper=a-0");
  await expect(page.locator(".paper-row").first()).toBeVisible();
  const rows = await page.locator(".paper-row").count();
  const visibleRows = await page.locator(".paper-row:visible").count();
  expect(rows).toBeGreaterThanOrEqual(50);
  expect(visibleRows).toBeGreaterThanOrEqual(8);
  const rowBox = await page.locator(".paper-row").first().boundingBox();
  expect(rowBox!.height).toBeLessThanOrEqual(84);

  // Detail panel is compact and collapses when nothing is selected.
  await page.goto("/");
  await expect(page.locator(".paper-row").first()).toBeVisible();
  const collapsed = await page.locator(".paper-details:visible").count();
  expect(collapsed).toBe(0);

  const widths: any[] = [];
  for (const width of [1920, 1440, 1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const theme of ["light", "dark"]) {
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
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      widths.push({ width, theme, overflow });
      expect(overflow).toBeLessThanOrEqual(1);
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?view=reader&paper=a-0&document=original");
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => {
      document.documentElement.dataset.theme = t;
    }, theme);
    await page.screenshot({
      path: resolve(root, `reader-1440-${theme}.png`),
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=reader&paper=a-0&document=original");
  await expect(page.locator(".page-count")).toHaveText("/ 100");
  const readerOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(readerOverflow).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: resolve(root, "reader-390-light.png"),
    animations: "disabled",
  });

  writeFileSync(
    resolve(root, "design-audit.json"),
    JSON.stringify({ audit, rows, visibleRows, rowHeight: rowBox!.height, widths, errors, violations, external }, null, 2),
    { mode: 0o600 },
  );
  expect(errors).toEqual([]);
  expect(violations).toEqual([]);
  expect(external).toEqual([]);
});