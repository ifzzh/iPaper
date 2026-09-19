import { test, expect } from "@playwright/test";

test("position read failure never overwrites history, stale detail and expired identity stay isolated", async ({
  page,
  context,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
  let writes = 0;
  await page.route("**/api/paper/c-0/reading-position", async (route) => {
    if (route.request().method() === "PUT") {
      writes++;
      await route.continue();
    } else await route.fulfill({ status: 503, json: { error: "unavailable" } });
  });
  await page.goto("/?view=reader&paper=c-0");
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await expect(
    page.getByText(
      "阅读位置暂时无法加载，本次阅读不会覆盖已保存位置；请重新加载重试。",
    ),
  ).toBeVisible();
  await page.getByLabel("页码", { exact: true }).fill("2");
  await page.getByLabel("页码", { exact: true }).press("Enter");
  await expect(
    page.locator('.pdf-panel [data-page="2"] .textLayer'),
  ).toContainText("page 2");
  await page.waitForTimeout(3500);
  expect(writes).toBe(0);
  await page.unroute("**/api/paper/c-0/reading-position");
  await page.getByRole("button", { name: "重新加载位置", exact: true }).click();
  await expect(
    page.getByText(
      "阅读位置暂时无法加载，本次阅读不会覆盖已保存位置；请重新加载重试。",
    ),
  ).toHaveCount(0);
  await page.goto("/?view=library");
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  await page.route("**/api/paper/c-5", async (route) => {
    await gate;
    await route.fulfill({ json: { id: "c-5", title: "STALE DETAIL" } });
  });
  await expect(page.locator(".paper-row")).toHaveCount(6);
  await page.locator(".paper-row").nth(0).click();
  await page.locator(".paper-row").nth(1).click();
  release();
  await expect(page.locator(".paper-details")).not.toContainText(
    "STALE DETAIL",
  );
  await context.clearCookies();
  await page.evaluate(() => dispatchEvent(new Event("focus")));
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".paper-row")).toHaveCount(0);
  await expect(page.locator("canvas")).toHaveCount(0);
});
