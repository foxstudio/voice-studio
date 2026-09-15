#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.INCREMENTAL_DUB_URL;
const project = process.env.INCREMENTAL_DUB_PROJECT;
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base) && project);
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
  ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(20000);
const errors = [];
const consoleErrors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
try {
  await page.goto(`${base}/video-localization?project_id=${project}`, { waitUntil: 'networkidle' });
  // The normal editor entry point is a right-click in the localized subtitle
  // track.  Its default command submits regeneration_mode=auto.
  const track = page.locator('[data-track-id="localizedSubtitles"]');
  await track.scrollIntoViewIfNeeded();
  await track.click({ button: 'right', position: { x: 30, y: 12 } });
  await page.getByRole('menuitem', { name: /^全部重做配音字幕(?:，|$)/ }).waitFor();
  const submit = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname.endsWith('/operations/dub-subtitles'));
  await page.getByRole('menuitem', { name: /^生成／更新配音字幕(?:，|$)/ }).click();
  assert.ok((await submit).ok());
  await page.locator('.task-row.running').filter({ hasText: '根据合成配音生成字幕' }).waitFor();
  assert.ok((await page.request.post(`${base}/api/__content_acceptance/release-dub-subtitle`)).ok());
  // The operation feed updates the visible running/history card. The final
  // caption switch remains available after a reload, proving persisted result.
  await page.locator('.task-row.history-row').filter({ hasText: '根据合成配音生成字幕' })
    .filter({ hasText: '已保存' }).waitFor({ timeout: 40000 });
  await page.reload({ waitUntil: 'networkidle' });
  const toggle = page.getByRole('button', { name: /^切换到(合成配音字幕|本土化上屏字幕)$/ });
  await toggle.waitFor();
  assert.equal(await toggle.isDisabled(), false);
  if (await toggle.getAttribute('aria-pressed') !== 'true') await toggle.click();
  await page.getByText('风格变了角色也变了', { exact: true }).first().waitFor();
  assert.deepEqual(errors, []);
  assert.deepEqual(consoleErrors, []);
  console.log(JSON.stringify({ incremental_dub_caption_browser: 'passed', checks: ['editor auto submit', 'running and completed task UI', 'caption result after reload'] }));
} catch (error) {
  console.error((await page.locator('body').innerText()).slice(-8000));
  throw error;
} finally {
  await browser.close();
}
