import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("empty library uploads a synthetic PDF, reads it and retains Reading List", async ({
  page,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "empty_reader", password: "workbench-test-pass" },
  });
  await page.goto("/?view=library");
  await expect(
    page.getByRole("heading", { name: "从第一篇论文开始" }),
  ).toBeVisible();
  await page
    .locator(".header-actions")
    .getByRole("button", { name: "导入文献" })
    .click();
  await page
    .getByLabel("导入文件")
    .setInputFiles(resolve("../tests/fixtures/workbench/translated.pdf"));
  await page.getByRole("button", { name: "开始导入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "任务中心", exact: true }),
  ).toBeVisible();
  await page
    .locator(".task-card")
    .filter({ hasText: "translated.pdf" })
    .click();
  await expect(
    page.getByRole("dialog").getByText("已完成", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "打开论文", exact: true }).click();
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await expect(page.locator(".page-count")).toHaveText("/ 2");
  const list = await (await page.request.get("/api/reading-list")).json();
  expect(list).toHaveLength(1);
  const id = list[0].id;
  await page.reload();
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  expect((await page.request.get("/api/paper/a-0")).status()).toBe(404);
  expect((await page.request.get("/api/paper/" + id + "/file")).status()).toBe(
    200,
  );
});
