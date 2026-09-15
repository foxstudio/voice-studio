import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import { resolve } from 'node:path';
const base = process.env.REFERENCE_EDITOR_URL;
const root = process.env.REFERENCE_EDITOR_ROOT;
if (!base || !root || [5173, 18000, 8000].includes(Number(new URL(base).port))) throw new Error('An isolated service and owned fixture root are required');
import assert from 'node:assert/strict';
const browser=await chromium.launch(isolatedBrowserOptions({channel:'chrome'}));const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(12000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
await page.goto(`${base}/generate`,{waitUntil:'networkidle'});
await page.getByRole('button',{name:'自定义',exact:true}).click();
const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'点击或拖拽导入参考音频或视频'}).click();await(await chooser).setFiles(resolve(root, 'sample.wav'));
await page.waitForFunction(()=>document.querySelector('.custom-voice-dropzone')?.textContent.includes('选择范围后使用选区'));
const before=await page.locator('.custom-voice-trim-readout').innerText();
await page.locator('a[href="/voice-library"]').first().click();await page.waitForURL('**/voice-library');await page.locator('a[href="/generate"]').first().click();await page.waitForURL('**/generate');
const after=await page.locator('.custom-voice-trim-readout').innerText();
await page.screenshot({path:resolve(root, 'reentry.png')});
assert.equal(after,before);assert.equal(await page.getByRole('button',{name:'播放选区',exact:true}).isEnabled(),true);
await page.getByRole('button',{name:'播放选区',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.readout-current')?.textContent!=='当前0.0 秒');
await page.getByRole('button',{name:'暂停选区播放',exact:true}).click();
await page.getByRole('textbox',{name:'台词文本',exact:true}).fill('固定测试台词');
const input=page.getByRole('slider',{name:'裁切入点',exact:true});await input.focus();await input.press('Home');for(let i=0;i<20;i++)await input.press('ArrowRight');
await page.waitForFunction(()=>document.querySelector('.readout-in')?.textContent.includes('2.0'));
const edited=await page.locator('.custom-voice-trim-readout').innerText();
await page.locator('a[href="/voice-library"]').first().click();await page.waitForURL('**/voice-library');await page.locator('a[href="/generate"]').first().click();await page.waitForURL('**/generate');
assert.equal(await page.locator('.custom-voice-trim-readout').innerText(),edited.replace(/当前\n[^\n]+/,'当前\n0.0 秒'));assert.match(edited,/待重新识别/);
for(let i=0;i<35;i++)await page.getByRole('button',{name:'放大时间轴',exact:true}).click();
assert.ok(Number((await page.locator('.trim-zoom-buttons span').innerText()).replace('x',''))<=20);
for(const width of [1087,390]) {await page.setViewportSize({width,height:1200});const collapse=page.getByRole('button',{name:'收起侧边栏',exact:true});if(await collapse.isVisible())await collapse.click();const closeNav=page.getByRole('button',{name:'关闭导航',exact:true});if(await closeNav.isVisible())await closeNav.click();if(width<760)await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);await page.locator('.custom-voice-trimmer').screenshot({path:resolve(root, `editor-${width}.png`)});const box=await page.locator('.custom-voice-trimmer').boundingBox();assert.ok(box.x>=0 && box.x+box.width<=width);}
const ticks=await page.locator('.custom-voice-timebar-ruler b').allTextContents();assert.ok(ticks.filter(Boolean).length>2);assert.deepEqual(errors,[]);console.log('PASS reentry, playback, ticks');
}finally {await browser.close();}
