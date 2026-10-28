import { test, expect } from "@playwright/test";
test("page and zoom survive settled rendering, navigation and a fresh context", async ({
  page,
  browser,
}) => {
  test.setTimeout(60000);
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
  await page.goto("/?view=reader&paper=c-0&document=original");
  await expect(page.locator(".page-count")).toHaveText("/ 100");
  await page
    .locator('.pdf-panel [data-page="1"] .pdf-page[data-rendered="true"]')
    .waitFor();
  await page.getByLabel("页码", { exact: true }).fill("5");
  await page.getByLabel("页码", { exact: true }).press("Enter");
  await page.getByLabel("缩放", { exact: true }).selectOption("0.75");
  await page.waitForTimeout(5000);
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("5");
  await expect
    .poll(async () =>
      page
        .locator(".pdf-scroll")
        .evaluate((h) =>
          Math.abs(
            h.querySelector('[data-page="5"]')!.getBoundingClientRect().top -
              h.getBoundingClientRect().top -
              20,
          ),
        ),
    )
    .toBeLessThan(3);
  await expect
    .poll(async () => {
      const v = await (
        await page.request.get("/api/paper/c-0/reading-position")
      ).json();
      return { page: v.original?.page, zoom: v.original?.zoom };
    })
    .toEqual({ page: 5, zoom: 0.75 });
  for (let turn = 0; turn < 4; turn++) {
    await page.getByRole("button", {name:"阅读更多操作",exact:true}).click();
    await page.getByLabel("旋转", { exact: true }).click();
    await page.waitForTimeout(250);
    await expect
      .poll(async () =>
        page
          .locator(".pdf-scroll")
          .evaluate((h) =>
            Math.abs(
              h.querySelector('[data-page="5"]')!.getBoundingClientRect().top -
                h.getBoundingClientRect().top -
                20,
            ),
          ),
      )
      .toBeLessThan(3);
  }
  await page.setViewportSize({ width: 1000, height: 850 });
  await page.getByLabel("缩放", { exact: true }).selectOption("width");
  await page.waitForTimeout(300);
  await expect
    .poll(async () =>
      page
        .locator(".pdf-scroll")
        .evaluate((h) =>
          Math.abs(
            h.querySelector('[data-page="5"]')!.getBoundingClientRect().top -
              h.getBoundingClientRect().top -
              20,
          ),
        ),
    )
    .toBeLessThan(3);
  await page.getByLabel("缩放", { exact: true }).selectOption("0.75");
  await page.waitForTimeout(3500);
  await page.goto("/?paper=c-0");
  const c = await browser.newContext({ baseURL: "http://127.0.0.2:7191" });
  await c.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
  const fresh = await c.newPage();
  await fresh.goto("/?view=reader&paper=c-0&document=original");
  await fresh
    .locator('.pdf-panel [data-page="5"] .pdf-page[data-rendered="true"]')
    .waitFor();
  await fresh.waitForTimeout(4000);
  await expect(fresh.getByLabel("页码", { exact: true })).toHaveValue("5");
  await expect(fresh.getByLabel("缩放", { exact: true })).toHaveValue("0.75");
  await c.close();
});
