import { test, expect } from "@playwright/test";
test("persisted translation history uses the real job identifier and opens terminal logs", async ({
  page,
}) => {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_two", password: "workbench-test-pass" },
  });
  const response = await page.request.get("/api/translations");
  const tasks = (await response.json()).tasks;
  expect(tasks[0].job_id).toBe("00000000-0000-4000-8000-000000000112");
  expect(tasks[0].task_id).toBeUndefined();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/?view=tasks");
  const completed = page.locator(".task-card").filter({ hasText: "已完成" });
  await expect(completed).toHaveCount(1);
  await completed.click();
  await expect(page.locator(".task-log")).toContainText("合成终态日志");
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});
