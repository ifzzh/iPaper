import {test,expect} from '@playwright/test';

test('administrator invite, unified registration and own profile remain usable',async({page,browser})=>{
 await page.request.post('/api/auth/login',{data:{username:'review_admin',password:'workbench-test-pass'}});
 await page.goto('/?view=settings&section=admin');
 await expect(page.getByRole('heading',{name:'用户管理',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'生成邀请码',exact:true}).click();
 const code=await page.locator('.one-time-code').textContent();expect(code).toBeTruthy();
 const context=await browser.newContext();const fresh=await context.newPage();
 await fresh.goto('/');await fresh.getByRole('button',{name:'使用邀请码注册',exact:true}).click();
 await fresh.getByLabel('账号',{exact:true}).fill('ui_registered');
 await fresh.getByLabel('密码',{exact:true}).fill('workbench-test-pass');
 await fresh.getByLabel('邀请码',{exact:true}).fill(code!);
 await fresh.getByRole('button',{name:'注册',exact:true}).click();
 await expect(fresh.getByText('账号已创建，请登录。')).toBeVisible();
 await fresh.getByLabel('密码',{exact:true}).fill('workbench-test-pass');
 await fresh.getByRole('button',{name:'登录',exact:true}).click();
 await expect(fresh.locator('.home-page')).toBeVisible();
 await fresh.goto('/?view=settings&section=account');
 await expect(fresh.getByRole('heading',{name:'个人资料',exact:true})).toBeVisible();
 expect((await fresh.request.get('/api/admin/users')).status()).toBe(403);
 await context.close();
});
