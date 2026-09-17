import { test, expect } from "@playwright/test";

test("stream failure, stop receiving, rotation and no-Range fallback retain clear boundaries", async ({
  page,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
  let files = 0;
  await page.route("**/api/paper/c-0/file", async (route) => {
    files++;
    const response = await route.fetch();
    const headers = { ...response.headers() };
    delete headers["accept-ranges"];
    delete headers["content-range"];
    await route.fulfill({ response, headers });
  });
  await page.goto("/?view=reader&paper=c-0");
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "中文文本选择",
  );
  expect(files).toBe(1);
  const canvas = page.locator(".pdf-panel canvas").first();
  expect(
    await canvas.evaluate((el: HTMLCanvasElement) =>
      el
        .getContext("2d")!
        .getImageData(0, 0, el.width, el.height)
        .data.some((v, i) => i % 4 !== 3 && v < 200),
    ),
  ).toBe(true);
  await page.getByLabel("旋转", { exact: true }).click();
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "中文文本选择",
  );
  await page.getByLabel("页码", { exact: true }).fill("100");
  await page.getByLabel("页码", { exact: true }).press("Enter");
  await expect(
    page.locator('.pdf-panel [data-page="100"] .textLayer'),
  ).toContainText("page 100");
  expect(await page.locator(".pdf-panel canvas").count()).toBeLessThan(12);
  await page.getByRole("checkbox").check();
  await page.getByLabel("你的问题").fill("mid-failure");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".chat-notice")).toContainText("未保存");
  await page.getByLabel("会话操作", { exact: true }).click();
  await page.getByRole("menuitem", { name: "新会话", exact: true }).click();
  await page.getByLabel("你的问题").fill("slow");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "停止接收", exact: true }).click();
  await expect(page.locator(".chat-notice")).toContainText(
    "服务端可能继续处理",
  );
  await page.getByLabel("收起问答", { exact: true }).click();
  await expect(page.locator(".chat-panel")).toHaveCount(0);
});
