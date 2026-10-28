import {test,expect} from '@playwright/test';

test('touch library keeps selection discoverable and work surfaces inside the viewport',async({browser})=>{
 const context=await browser.newContext({baseURL:'http://127.0.0.2:7191',viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 try{
  const page=await context.newPage();await page.request.post('/api/auth/login',{data:{username:'reader_pdf',password:'workbench-test-pass'}});
  await page.goto('/?view=library');const row=page.locator('.paper-row').first();await expect(row).toBeVisible();
  const checkbox=row.getByRole('checkbox');await expect(checkbox).toHaveCSS('opacity','1');await checkbox.check();await expect(checkbox).toBeChecked();
  await page.getByRole('button',{name:'卡片视图',exact:true}).click();await expect(page.locator('.library-cards')).toBeVisible();
  for(const selector of ['.library-list-panel','.scope-tabs','.list-toolbar','.paper-row']){
   const rect=await page.locator(selector).first().boundingBox();expect(rect).not.toBeNull();expect(rect!.x).toBeGreaterThanOrEqual(0);expect(rect!.x+rect!.width).toBeLessThanOrEqual(390);
  }
 }finally{await context.close();}
});

test('reader streams a small PDF once, reuses it after auth, and refuses revoked access',async({page})=>{
 await page.request.post('/api/auth/login',{data:{username:'reader_pdf',password:'workbench-test-pass'}});
 const files:{method:string;range:string|undefined}[]=[];let denied=false;
 await page.route('**/api/paper/c-0/file',async route=>{
  files.push({method:route.request().method(),range:route.request().headers().range});
  if(denied&&route.request().method()==='HEAD')return route.fulfill({status:403,json:{error:'forbidden'}});
  return route.continue();
 });
 await page.goto('/?view=reader&paper=c-0&document=original');
 await expect(page.locator('.pdf-page[data-rendered="true"] .textLayer').first()).toContainText('中文文本选择');
 expect(files.filter(x=>x.method==='GET')).toEqual([{method:'GET',range:undefined}]);
 const more=page.getByRole('button',{name:'阅读更多操作',exact:true});await more.press('ArrowDown');
 await expect(page.getByRole('menuitem',{name:'页级记录',exact:true})).toBeFocused();
 await page.keyboard.press('ArrowDown');await expect(page.getByRole('menuitem',{name:'旋转',exact:true})).toBeFocused();
 await page.keyboard.press('Escape');await expect(more).toBeFocused();
 await page.getByRole('button',{name:'文献库',exact:true}).click();
 await page.getByRole('tab').first().click();
 await expect(page.locator('.pdf-page[data-rendered="true"] .textLayer').first()).toContainText('中文文本选择');
 expect(files.filter(x=>x.method==='GET')).toHaveLength(1);expect(files.filter(x=>x.method==='HEAD')).toHaveLength(2);
 denied=true;await page.getByRole('button',{name:'文献库',exact:true}).click();await page.getByRole('tab').first().click();
 await expect(page.locator('.pdf-page[data-rendered="true"]')).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'欢迎回来'})).toBeVisible();
 expect(files.filter(x=>x.method==='GET')).toHaveLength(1);
});

test('a delayed translation catalog does not first open the legacy file',async({page})=>{
 await page.request.post('/api/auth/login',{data:{username:'reader_pdf',password:'workbench-test-pass'}});
 let unblock!:()=>void;const barrier=new Promise<void>(r=>unblock=r);const files:string[]=[];
 await page.route('**/api/paper/c-0/results',async route=>{const response=await route.fetch();await barrier;await route.fulfill({response});});
 page.on('request',r=>{if(r.method()==='GET'&&new URL(r.url()).pathname.endsWith('/file'))files.push(new URL(r.url()).pathname);});
 await page.goto('/?view=reader&paper=c-0&document=translated');
 await expect(page.locator('.reader-preparing')).toBeVisible();expect(files).toEqual([]);
 unblock();await expect(page.locator('.pdf-page[data-rendered="true"] .textLayer').first()).toBeVisible();
 expect(files).toHaveLength(1);expect(files[0]).toMatch(/^\/api\/documents\/.+\/file$/);
});
