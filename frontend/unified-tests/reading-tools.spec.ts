import { test, expect } from "@playwright/test";
import {writeFile} from "node:fs/promises";
async function login(page: any) {
  await page.request.post("/api/auth/login", {
    data: { username: "reader_pdf", password: "workbench-test-pass" },
  });
}
test("300-page PDF full search, exact highlight, outline and durable bookmark", async ({
  page,
}) => {
  const errors: string[] = [],
    csp: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (/Refused to|Content Security Policy/.test(m.text())) csp.push(m.text());
  });
  await login(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?view=reader&paper=c-0&document=original");
  await expect(page.locator(".page-count")).toHaveText("/ 300");
  await expect(
    page.locator('.pdf-panel [data-page="1"] .textLayer'),
  ).toContainText("iPaper reading tools");
  await page.getByLabel("搜索全文", { exact: true }).click();
  const input = page.getByLabel("搜索 PDF 全文");
  const searchStart = Date.now();
  const heapBefore = await page.evaluate(() => (performance as any).memory?.usedJSHeapSize || null);
  await input.fill("UniqueEndEvidence739");
  await expect(page.getByText("扫描结束：300 / 300 页 · 1 处匹配")).toBeVisible(
    { timeout: 60000 },
  );
  await writeFile(new URL("../../.devnotes/reading-tools-performance.json",import.meta.url),JSON.stringify({pdfPages:300,searchMs:Date.now()-searchStart,mainPageHeapBefore:heapBefore,mainPageHeapAfter:await page.evaluate(() => (performance as any).memory?.usedJSHeapSize || null),heapScope:"Chromium page JS heap only, excluding workers, GPU and canvas native allocations",thresholdClaim:false},null,2));
  await input.press("Enter");
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("299");
  await expect(
    page.locator('.pdf-panel [data-page="299"] .textLayer'),
  ).toContainText("UniqueEndEvidence739");
  await expect(page.locator(".pdf-search-highlight").first()).toBeVisible();
  await page.screenshot({
    path: "../.devnotes/reading-tools-search-1440-light.png",
  });
  await page.getByLabel("缩放", { exact: true }).selectOption("1");
  await page.getByLabel("旋转", { exact: true }).click();
  await expect(page.locator(".pdf-search-highlight").first()).toBeVisible();
  await input.fill("definitely-absent-phrase");
  await input.fill("wrapped paragraph continues onto the next line");
  await expect(page.getByText("扫描结束：300 / 300 页 · 1 处匹配")).toBeVisible(
    { timeout: 60000 },
  );
  await input.press("Enter");
  await expect(page.locator(".pdf-search-highlight")).not.toHaveCount(0);
  await page.getByRole("button", { name: "书签", exact: true }).click();
  await page.getByRole("button", { name: "保存当前位置" }).click();
  await page.getByLabel("书签名称").fill("后部实验位置");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".bookmark-row")).toContainText("后部实验位置");
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  await page.getByLabel("书签名称").fill("末页前的证据");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "书签", exact: true }).click();
  await expect(page.locator(".bookmark-row")).toContainText("末页前的证据");
  await page.getByRole("button", { name: "末页前的证据" }).click();
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("299");
  await page.getByRole("button", { name: "目录", exact: true }).click();
  await page.getByRole("button", { name: "Introduction 第 1 页" }).click();
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("1");
  await page.goto("/?view=reader&paper=c-1&document=original");
  await page.getByLabel("搜索全文", { exact: true }).click();
  await page.getByLabel("搜索 PDF 全文").fill("scan");
  await expect(page.getByText(/此 PDF 没有可搜索文字/)).toBeVisible();
  await page.goto("/?view=reader&paper=c-2&document=original");
  await expect(page.getByText(/没有可用目录/)).toBeVisible();
  expect(errors).toEqual([]);
  expect(csp).toEqual([]);
});

test("structure full search, cached paragraph, explicit selection and table viewer", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?view=reader&paper=c-4&document=original");
  await expect(page.locator(".pdf-panel .textLayer").first()).toContainText(
    "Synthetic reader validation",
  );
  await page.getByLabel("阅读内容").selectOption("structure");
  await page.getByRole("button", { name: "生成结构内容", exact: true }).click();
  await page.getByRole("button", { name: "开始生成", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.getByRole("button", { name: "关闭", exact: true }).first().click();
  await expect(page.locator(".structure-block").first()).toBeVisible();
  const selected = await page.getByLabel("结构结果版本").inputValue();
  const result = (
    await (await page.request.get(`/api/results/${selected}`)).json()
  ).result;
  await page.getByLabel("搜索全文", { exact: true }).click();
  await page.getByLabel("搜索结构全文").fill("controlled experiment");
  await expect(page.getByText(/已扫描 8 \/ 8 块/)).toBeVisible();
  await page.getByRole("button", { name: "下一个匹配", exact: true }).click();
  await expect(page.locator(".search-target-block")).toContainText(
    "controlled experiment",
  );
  await page.getByRole("button", { name: "关闭阅读导航" }).click();
  await page
    .getByRole("button", { name: "展开表格", exact: true })
    .first()
    .click();
  await expect(page.getByRole("dialog").locator("table")).toContainText(
    "Baseline",
  );
  await page.getByLabel("放大图表").click();
  await page.getByLabel("关闭图表查看").click();
  await expect(page.locator(".structure-scroll")).toBeVisible();
  const paragraph = page
    .locator(".structure-paragraph")
    .filter({ hasText: "controlled experiment" })
    .first();
  await paragraph.evaluate((node) => {
    const text = node.querySelector('[data-source-start="0"]')!.firstChild!;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 24);
    const s = getSelection()!;
    s.removeAllRanges();
    s.addRange(range);
  });
  await paragraph.dispatchEvent("mouseup");
  await page
    .locator(".selection-actions")
    .getByRole("button", { name: "翻译", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("fixture");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "翻译", exact: true })
    .click();
  await expect(page.locator(".selection-result")).toContainText(
    "合成划词译文",
    { timeout: 20000 },
  );
  await page.screenshot({
    path: "../.devnotes/reading-tools-selection-1440-light.png",
  });
  await page.getByRole("dialog").getByLabel("关闭", { exact: true }).click();
  const jobsBefore = (
    await (await page.request.get("/api/processing/jobs")).json()
  ).jobs;
  await page.getByLabel("阅读内容").selectOption("original");
  await expect(
    page.locator(".pdf-panel .pdf-page[data-rendered=true]").first(),
  ).toBeVisible();
  const nav = await (
    await page.request.get(`/api/results/${selected}/navigation?page=1`)
  ).json();
  const hit = nav.items.find((i: any) => i.precision === "region");
  expect(hit).toBeTruthy();
  // The normalized Worker source is in PDF coordinates. Original test page has
  // a verified 595 x 842 MediaBox and no rotation; click its verified first region.
  const rect = hit.regions[0].rect;
  const surface = page.locator('.pdf-panel [data-page="1"] .pdf-page');
  const box = await surface.boundingBox();
  expect(box).not.toBeNull();
  await surface.click({
    position: {
      x: ((rect[0] + rect[2]) / 2 / 595) * box!.width,
      y: ((842 - (rect[1] + rect[3]) / 2) / 842) * box!.height,
    },
  });
  await expect(
    page.getByRole("region", { name: "段落已有译文" }),
  ).toContainText("合成译文");
  await page.getByLabel("关闭段落译文").click();
  expect(
    (await (await page.request.get("/api/processing/jobs")).json()).jobs,
  ).toEqual(jobsBefore);
  await page.getByLabel("阅读内容").selectOption("structure");
  await expect(page.getByLabel("结构结果版本")).toHaveValue(result.id);
  expect(errors).toEqual([]);
});


test('an explicit PDF deep link wins over a previously saved analysis panel', async({page}) => {
  await login(page);
  await page.route('**/api/paper/c-0/understanding-position',route => route.request().method()==='GET' ? route.fulfill({json:{kind:'interpretation',resultId:'legacy',offset:0.5}}) : route.continue());
  await page.goto('/?view=reader&paper=c-0&document=original');
  await expect(page.locator('.page-count')).toHaveText('/ 300');
  await expect(page.locator('.pdf-panel .textLayer').first()).toContainText('iPaper reading tools');
  await expect(page.locator('.understanding-scroll')).toHaveCount(0);
});
