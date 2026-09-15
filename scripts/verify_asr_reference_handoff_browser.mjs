import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const base = process.env.TIMELINE_EDIT_URL;
const project = process.env.TIMELINE_EDIT_PROJECT;
assert.ok(base && project && ![5173, 18000].includes(Number(new URL(base).port)));
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
try {
  const prefix = `${base}/api/projects/${project}/video-localization`;
  const draft = await (await page.request.get(prefix)).json();
  draft.cues[0].end_ms = 1000;
  draft.cues[0].source_duration_ms = 1000;
  const saved = await page.request.put(prefix, { data: draft });
  assert.ok(saved.ok(), await saved.text());
  for (const width of [1440, 900]) {
    await page.setViewportSize({ width, height: 1100 });
    await page.goto(`${base}/video-localization?project_id=${project}`, { waitUntil: 'networkidle' });
    await page.reload({ waitUntil: 'networkidle' });
    const subtitle = page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]');
    await subtitle.waitFor();
    const box = await subtitle.boundingBox();
    await subtitle.click({ position: { x: box.width - 8, y: box.height / 2 } });
    await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
    const response = page.waitForResponse(r => r.url().includes('/tts/handoff-preview/'));
    await page.getByRole('button', { name: /调整当前片段的配音参数|送到语音合成/ }).click();
    const handoff = await response;
    assert.ok(handoff.ok(), await handoff.text());
    const request = await handoff.json();
    assert.equal(request.custom_reference_trim_start_ms, 0);
    assert.equal(request.custom_reference_trim_end_ms, 1000);
    assert.equal(request.ref_text, 'Camera movement');
    await page.waitForURL('**/generate**');
    await page.locator('textarea').first().waitFor();
    const values = await page.locator('textarea').evaluateAll(els => els.map(el => el.value));
    assert.ok(values.includes('Camera movement'), JSON.stringify(values));
    assert.match(await page.locator('body').innerText(), /选区\s*1(?:\.0+)?\s*秒/);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ asrReferenceHandoff: 'passed', rangeMs: [0, 1000], text: 'Camera movement', widths: [1440, 900] }));
} finally { await browser.close(); }
