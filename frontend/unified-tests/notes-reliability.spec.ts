import { test, expect, type Page } from "@playwright/test";
const notePath = "/api/paper/c-4/reading/note";
async function headers(page: Page) {
  return {'X-CSRF-Token': (await page.context().cookies()).find(c=>c.name==='paperpilot_csrf')!.value};
}
async function note(page: Page) { return (await (await page.request.get(notePath)).json()).note; }
async function evidence(page: Page, name: string, value: unknown) {
  await test.info().attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
  await test.info().attach(name + '-view', { body: await page.screenshot(), contentType: 'image/png' });
}
async function openNote(page: Page) {
  const h=await headers(page);
  let current=await note(page);
  for(const conflict of current.conflicts || []) {
    if(!String(conflict.currentRevision||'').startsWith('resolved:')) {
      await page.request.post(`${notePath}/conflicts/${conflict.id}`,{headers:h,data:{choice:'current',revision:current.revision}});
    }
  }
  current=await note(page);
  const reset=await page.request.put(notePath,{headers:h,data:{markdown:'baseline',revision:current.revision}});
  expect(reset.status()).toBe(200);
  await page.goto('/?paper=c-4&view=reader');
  await expect(page.locator('.textLayer span').first()).toBeVisible({timeout:30000});
  await page.getByRole('tab',{name:'笔记',exact:true}).click();
  await expect(page.locator('.note-textarea')).toHaveValue('baseline');
}

test('an unresolved conflict survives a panel remount without silently replacing the remote copy', async ({page}) => {
  await openNote(page);
  const remote = 'REMOTE MUST REMAIN UNTIL AN EXPLICIT CONFLICT DECISION';
  const local = 'LOCAL CONFLICT DRAFT MUST BE KEPT SEPARATELY';
  const initial = await note(page);
  const update = await page.request.put(notePath, {headers: await headers(page), data: {markdown: remote, revision: initial.revision}});
  expect(update.status()).toBe(200);
  await page.locator('.note-textarea').fill(local);
  await expect(page.locator('.note-editor').getByRole('button', {name:'保留我的草稿', exact:true})).toBeVisible();
  const before = await note(page);
  let decisions = 0;
  const writes: unknown[] = [];
  page.on('request', r => {
    if (r.method() === 'POST' && r.url().includes('/reading/note/conflicts/')) decisions += 1;
    if (r.method() === 'PUT' && r.url().endsWith(notePath)) writes.push(r.postDataJSON());
  });
  await page.getByRole('tab', {name:'问答',exact:true}).click();
  await page.getByRole('tab', {name:'笔记',exact:true}).click();
  await page.waitForTimeout(1700);
  const after = await note(page);
  await evidence(page, 'edge-conflict-remount', {before, after, decisions, writes, editor: await page.locator('.note-textarea').inputValue(), status: await page.locator('.note-status').innerText()});
  expect(decisions).toBe(0);
  expect(after.markdown).toBe(remote);
  await expect(page.locator('.note-textarea')).toHaveValue(local);
  await expect(page.locator('.note-editor').getByRole('button', {name:'保留我的草稿',exact:true})).toBeVisible();
  await page.getByRole('button', {name:'收起问答',exact:true}).click();
  await page.getByRole('button', {name:'论文问答',exact:true}).click();
  await page.getByRole('tab', {name:'笔记',exact:true}).click();
  await expect(page.locator('.note-textarea')).toHaveValue(local);
  await page.getByRole('combobox', {name:'阅读内容',exact:true}).selectOption('structure');
  await page.getByRole('combobox', {name:'阅读内容',exact:true}).selectOption('original');
  await expect(page.locator('.textLayer span').first()).toBeVisible();
  await page.getByRole('tab', {name:'笔记',exact:true}).click();
  await expect(page.locator('.note-textarea')).toHaveValue(local);
  await expect(page.locator('.note-status')).toContainText('待选择');
  await page.waitForTimeout(1700);
  expect((await note(page)).markdown).toBe(remote);
  expect(decisions).toBe(0);
  expect(writes).toHaveLength(0);
});

for (const choice of ['保留我的草稿', '保留服务器版本']) for (const entry of ['.note-editor', '.note-conflict-banner'])
test(`new typing survives an in-flight ${choice} decision from ${entry}`, async ({page}) => {
  await openNote(page);
  const initial = await note(page);
  expect((await page.request.put(notePath, {headers: await headers(page), data: {markdown:'REMOTE before decision',revision:initial.revision}})).status()).toBe(200);
  await page.locator('.note-textarea').fill('B MY DRAFT WHEN I CLICKED');
  const decision = page.locator(entry).getByRole('button', {name:choice,exact:true});
  await expect(decision).toBeVisible();
  let release!: () => void, captured!: () => void;
  const gate = new Promise<void>(r => release=r), ready=new Promise<void>(r=>captured=r);
  let responseStatus: number | null = null;
  await page.route('**/api/paper/c-4/reading/note/conflicts/*', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch();
    responseStatus = response.status();
    captured();
    await gate;
    await route.fulfill({response});
  });
  try {
    await decision.click();
    await ready;
    const newest = 'C NEW TEXT TYPED AFTER CLICK BEFORE RESPONSE MUST SURVIVE';
    await expect(page.locator('.note-textarea')).toBeEditable();
    await page.locator('.note-textarea').fill(newest);
    const whilePending = await page.locator('.note-textarea').inputValue();
    release();
    await page.waitForTimeout(1900);
    const afterResponse = await page.locator('.note-textarea').inputValue();
    await page.getByRole('tab', {name:'问答',exact:true}).click();
    await page.getByRole('tab', {name:'笔记',exact:true}).click();
    await page.waitForTimeout(400);
    await evidence(page, 'edge-decision-newer-input', {responseStatus, whilePending, afterResponse, afterPanelRemount:await page.locator('.note-textarea').inputValue(), server: await note(page), status: await page.locator('.note-status').innerText()});
    expect(responseStatus).toBe(200);
    await expect(page.locator('.note-textarea')).toHaveValue(newest, {timeout:1000});
    expect((await note(page)).markdown).toBe(newest);
  } finally { release(); }
});

test('typing during Retry-After waits without a synchronous save-pump loop', async ({page}) => {
  await openNote(page);
  const pageErrors: string[] = [];
  let errorCount = 0, puts = 0;
  const requests: unknown[] = [];
  page.on('pageerror', err => { errorCount += 1; if (pageErrors.length < 4) pageErrors.push(String(err)); });
  const start = Date.now();
  await page.route('**/api/paper/c-4/reading/note', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    puts += 1;
    requests.push({at:Date.now()-start, body:route.request().postDataJSON()});
    if (puts === 1) return route.fulfill({status:429, contentType:'application/json', headers:{'Retry-After':'8'}, body:JSON.stringify({error:'rate_limit_exceeded'})});
    return route.continue();
  });
  await page.locator('.note-textarea').fill('A triggers a bounded synthetic 429');
  await expect(page.locator('.note-status')).toContainText('保存失败');
  await page.locator('.note-textarea').fill('B LATEST INPUT DURING RETRY AFTER');
  await page.waitForTimeout(1900);
  const duringWait = {elapsed:Date.now()-start, puts, errorCount, editor:await page.locator('.note-textarea').inputValue(),status:await page.locator('.note-status').innerText(), server:(await note(page)).markdown};
  await page.waitForTimeout(6500);
  await evidence(page, 'edge-retry-after-input', {duringWait, elapsed:Date.now()-start, puts, requests, errorCount, pageErrors, editor:await page.locator('.note-textarea').inputValue(), status:await page.locator('.note-status').innerText(), server:(await note(page)).markdown});
  expect(duringWait.puts).toBe(1);
  expect(pageErrors).toEqual([]);
  await expect(page.locator('.note-textarea')).toHaveValue('B LATEST INPUT DURING RETRY AFTER');
  await expect(page.locator('.note-status')).toContainText('已保存');
  expect((await note(page)).markdown).toBe('B LATEST INPUT DURING RETRY AFTER');
  expect(puts).toBe(2);
});

test('a translated PDF excerpt opened from the original switches to the correct document', async ({page}) => {
  const h = await headers(page);
  const base = '/api/paper/c-0/reading';
  const docResponse = await page.request.post('/api/paper/c-0/reading-document', {headers:h, data:{document:'translated'}});
  expect(docResponse.status()).toBe(200);
  const doc = (await docResponse.json()).document;
  const created = await page.request.post(base+'/annotations', {headers:h, data:{documentId:doc.id, kind:'highlight', excerpt:'Synthetic translated document excerpt', color:'violet', anchor:{mode:'pdf',page:1,rects:[{x:.1,y:.12,w:.5,h:.04}]}}});
  expect(created.status()).toBe(201);
  const annotation = (await created.json()).annotation;
  const current = (await (await page.request.get(base+'/note')).json()).note;
  expect((await page.request.post(base+'/note/excerpts', {headers:h,data:{annotationId:annotation.id,revision:current.revision}})).status()).toBe(200);
  await page.goto('/?paper=c-0&view=reader&content=original');
  await expect(page.locator('.textLayer span').first()).toBeVisible({timeout:30000});
  await expect(page.getByRole('combobox',{name:'阅读内容',exact:true})).toHaveValue('original');
  await page.getByRole('tab',{name:'笔记',exact:true}).click();
  await page.locator('.note-sources summary').click();
  await page.locator('.note-sources').getByRole('button',{name:/^回到来源/}).click();
  await expect(page.locator('.annotation-detail')).toBeVisible();
  await page.waitForTimeout(350);
  await evidence(page, 'edge-translated-excerpt-target', {doc, annotation, url:page.url(), mode:await page.getByRole('combobox',{name:'阅读内容',exact:true}).inputValue(), detail:await page.locator('.annotation-detail').innerText(), visiblePdfText:(await page.locator('.textLayer').allTextContents()).join('\n').slice(0,1000)});
  await expect(page.getByRole('combobox',{name:'阅读内容',exact:true})).toHaveValue('translated', {timeout:1000});
  await expect(page.locator('.annotation-detail')).toContainText('已定位到来源');
  await expect(page.locator('.textLayer').first()).toContainText('合成译文样例');
});

test('excerpt navigation selects the exact structure side and PDF while keeping an unsaved note', async ({page}) => {
  const h = await headers(page);
  const base = '/api/paper/c-4/reading';
  // The isolated server supplies synthetic parsing/translation, never a paid provider.
  const preview = await (await page.request.post('/api/paper/c-4/processing/preview', {headers:h, data:{}})).json();
  const started = await (await page.request.post('/api/paper/c-4/processing/jobs', {
    headers:h, data:{preflightId:preview.preflightId, kind:'parse_translate'},
  })).json();
  await expect.poll(async () => (await (await page.request.get(`/api/processing/jobs/${started.job.id}`)).json()).job.status,
    {timeout:60000}).toBe('completed');
  const job = (await (await page.request.get(`/api/processing/jobs/${started.job.id}`)).json()).job;
  const results = (await (await page.request.get('/api/paper/c-4/results')).json()).results;
  const result = results.find((r:any) => r.id === job.resultId);
  const blocks = (await (await page.request.get(`/api/results/${result.id}/blocks`)).json()).blocks;
  const block = blocks.find((b:any) => b.text?.length > 10 && b.translation?.content?.text?.length > 10);
  expect(block).toBeTruthy();
  const annotations: any[] = [];
  for (const translated of [false, true]) {
    const text = translated ? block.translation.content.text : block.text;
    const created = await page.request.post(base + '/annotations', {headers:h, data:{
      documentId:result.documentId, resultId:result.id, kind:'highlight', excerpt:text.slice(0,10),
      anchor:{mode:'structure', blockId:block.id, field:'text', start:0, end:10,
        ...(translated ? {translationRevision:block.translation.revision} : {})},
    }});
    expect(created.status()).toBe(201);
    annotations.push((await created.json()).annotation);
  }
  const doc = (await (await page.request.post('/api/paper/c-4/reading-document', {headers:h, data:{}})).json()).document;
  const created = await page.request.post(base + '/annotations', {headers:h, data:{
    documentId:doc.id, kind:'page_note', comment:'Return to the exact PDF page',
    anchor:{mode:'page',page:2},
  }});
  expect(created.status()).toBe(201);
  annotations.push((await created.json()).annotation);
  for (const annotation of annotations) {
    const current = await note(page);
    expect((await page.request.post(base + '/note/excerpts', {headers:h,
      data:{annotationId:annotation.id,revision:current.revision}})).status()).toBe(200);
  }
  const entries = (await note(page)).entries;
  await page.goto('/?paper=c-4&view=reader&content=original');
  await expect(page.locator('.textLayer span').first()).toBeVisible();
  await page.evaluate(() => { (window as any).__noteNavigationSentinel = 'same document'; });
  await page.getByRole('tab',{name:'笔记',exact:true}).click();
  await page.route('**/api/paper/c-4/reading/note', route => route.request().method() === 'PUT'
    ? route.fulfill({status:503, contentType:'application/json', body:'{"error":"synthetic_offline"}'}) : route.continue());
  const draft = 'Unsaved PDF draft survives both structure sides and the return to PDF';
  await page.locator('.note-textarea').fill(draft);
  await expect(page.locator('.note-status')).toContainText('保存失败');
  for (const [index, annotation] of annotations.entries()) {
    await page.getByRole('tab',{name:'笔记',exact:true}).click();
    await expect(page.locator('.note-textarea')).toHaveValue(draft);
    const sources = page.locator('.note-sources');
    if (!(await sources.evaluate(element => (element as HTMLDetailsElement).open))) await sources.locator('summary').click();
    const entryIndex = entries.findIndex((entry:any) => entry.annotationId === annotation.id);
    await sources.locator('li').nth(entryIndex).getByRole('button',{name:/^回到来源/}).click();
    await expect(page.locator('.annotation-detail')).toContainText('已定位到来源');
    if (index < 2) {
      await expect(page.getByRole('combobox',{name:'阅读内容',exact:true})).toHaveValue('structure');
      expect(new URL(page.url()).searchParams.get('result')).toBe(result.id);
      const language = index === 0 ? 'original' : 'translated';
      await expect(page.locator(`[data-block="${block.id}"] .block-language[data-language="${language}"]`)).toBeVisible();
      await expect(page.locator(`[data-block="${block.id}"] .reader-highlight`).first()).toBeVisible();
      if (index === 0) {
        await page.getByRole('button', {name:'原文对照',exact:true}).first().click();
        await expect(page.locator('.structure-comparison .pdf-page[data-rendered="true"]').first()).toBeVisible();
        // A structure annotation target must not leak into the embedded PDF
        // and reopen another note panel over the source comparison.
        await expect(page.locator('.structure-comparison .reader-chat')).toHaveCount(0);
        await page.getByRole('button', {name:'关闭原文对照',exact:true}).click();
        await page.locator('.structure-subtoolbar').getByRole('button', {name:'论文问答',exact:true}).click();
      }
    } else {
      await expect(page.getByRole('combobox',{name:'阅读内容',exact:true})).toHaveValue('original');
      await expect(page.getByRole('spinbutton',{name:'页码',exact:true})).toHaveValue('2');
      await expect(page.locator('.page-host[data-page="2"] .textLayer')).toContainText('page 2');
    }
    expect(await page.evaluate(() => (window as any).__noteNavigationSentinel)).toBe('same document');
  }
  await page.unroute('**/api/paper/c-4/reading/note');
  await page.getByRole('tab',{name:'笔记',exact:true}).click();
  await expect(page.locator('.note-textarea')).toHaveValue(draft);
  await page.locator('.note-textarea').fill(draft + ' — recovered');
  await expect(page.locator('.note-status')).toContainText('已保存');
  expect((await note(page)).markdown).toBe(draft + ' — recovered');
  for (const annotation of annotations) {
    expect((await page.request.delete(base + '/annotations/' + annotation.id,
      {headers:h,data:{revision:annotation.revision}})).status()).toBe(200);
  }
});
