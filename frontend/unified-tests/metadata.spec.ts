import {test,expect} from '@playwright/test';

test('metadata edit, protected clear, bibliography, batch and responsive details',async({page},testInfo)=>{
 const faults:string[]=[];page.on('pageerror',e=>faults.push(e.message));
 await page.goto('/');await page.getByLabel('账号',{exact:true}).fill('reader_pdf');await page.getByLabel('密码',{exact:true}).fill('workbench-test-pass');await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.locator('.paper-row').first()).toBeVisible();await page.locator('.paper-row').first().click();
 const id=new URL(page.url()).searchParams.get('paper')!;
 const original=await (await page.request.get(`/api/paper/${id}`)).json();
 await page.getByLabel('编辑元数据').click();const dialog=page.getByRole('dialog');
 await dialog.getByRole('textbox',{name:'标题',exact:true}).fill('元数据验收 · A & B 的可靠研究');
 await dialog.getByRole('textbox',{name:'作者摘要',exact:true}).fill('显式编辑后清空');await dialog.getByRole('textbox',{name:'作者摘要',exact:true}).fill('');
 await dialog.getByLabel('DOI',{exact:true}).fill('10.9999/synthetic');
 await dialog.getByLabel('会议／期刊',{exact:true}).fill('用户明确的会议');
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await expect(dialog).toHaveCount(0);
 await expect(page.locator('.detail-title')).toHaveText('元数据验收 · A & B 的可靠研究');
 const meta=await (await page.request.get(`/api/paper/${id}/metadata`)).json();expect(meta.provenance.abstract.manual).toBe(true);expect(meta.fields.abstract).toBe('');
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'BibTeX',exact:true}).click()]);await download.saveAs(testInfo.outputPath('iPaper-current.bib'));
 const citation=await (await page.request.get(`/api/paper/${id}/bibtex`)).json();expect(citation.bibtex).toContain('用户明确的会议');expect(download.suggestedFilename()).toContain('iPaper');
 await page.getByRole('button',{name:'补全信息',exact:true}).click();await expect(page.locator('.metadata-status')).not.toContainText('正在补全',{timeout:45000});
 await page.reload();await expect(page.locator('.detail-title')).toContainText('A & B');
 expect((await (await page.request.get(`/api/paper/${id}/metadata`)).json()).fields.abstract).toBe('');
 const after=await (await page.request.get(`/api/paper/${id}`)).json();for(const key of ['id','file_path','filename'])expect(after[key]).toEqual(original[key]);
 for(const scheme of ['light','dark'] as const){if((await page.locator('html').getAttribute('data-theme'))!==scheme)await page.getByLabel('切换浅深主题').click();for(const width of [1920,1440,390]){await page.setViewportSize({width,height:1000});await page.screenshot({path:testInfo.outputPath(`${scheme}-${width}-metadata.png`),fullPage:true});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true)}}
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'选择当前页',exact:true}).click();await page.locator('.batch-toolbar').getByRole('button',{name:'补全信息',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('已入库论文');await page.getByRole('button',{name:'开始补全',exact:true}).click();await expect(page.getByRole('heading',{name:'任务中心',exact:true})).toBeVisible();
 await page.getByRole('button',{name:/论文信息补全/}).first().click();await expect(page.getByRole('dialog')).toContainText('任务日志');
 expect(faults).toEqual([]);
});

test('two tabs retain a conflicting manual draft and an explicit empty field',async({page,context})=>{
 await page.goto('/');await page.getByLabel('账号',{exact:true}).fill('reader_pdf');await page.getByLabel('密码',{exact:true}).fill('workbench-test-pass');await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.locator('.paper-row').first().click();const target=page.url(),second=await context.newPage();await second.goto(target);
 for(const tab of [page,second])await tab.getByLabel('编辑元数据').click();
 await page.getByRole('dialog').getByRole('textbox',{name:'标题',exact:true}).fill('第一标签已保存的标题');
 await second.getByRole('dialog').getByRole('textbox',{name:'标题',exact:true}).fill('第二标签保留的草稿');
 await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();
 await second.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();
 await expect(second.getByRole('dialog')).toContainText('草稿已保留');await expect(second.getByRole('dialog').getByRole('textbox',{name:'标题',exact:true})).toHaveValue('第二标签保留的草稿');
 await second.getByRole('button',{name:'重新载入信息',exact:true}).click();await expect(second.getByRole('dialog').getByRole('textbox',{name:'标题',exact:true})).toHaveValue('第一标签已保存的标题');
 await second.close();
});
