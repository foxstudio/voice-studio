#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.TIMELINE_EDIT_URL;
const project = process.env.TIMELINE_EDIT_PROJECT;
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base)
  && ![5173, 18000, 65335].includes(Number(new URL(base).port)) && project);
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
  ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(15000);
const errors = [];
const consoleErrors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') consoleErrors.push(message.text());
});
const prefix = `${base}/api/projects/${project}/video-localization`;
const read = async () => (await (await page.request.get(`${prefix}/workspace`)).json()).draft;
const clip = id => page.locator(`[data-audio-clip-id="${id}"]`);
const dub = draft => draft.timeline_clips.filter(item => item.track_id === 'dub');
const shape = clips => clips.map(({ clip_id, start_ms, end_ms, source_start_ms, source_end_ms, dub_lane }) =>
  ({ clip_id, start_ms, end_ms, source_start_ms, source_end_ms, dub_lane })).sort((a, b) => a.clip_id.localeCompare(b.clip_id));
async function savedAction(action) {
  const response = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/timeline-edit'));
  await action();
  const saved = await response;
  assert.ok(saved.ok(), await saved.text());
  await page.waitForFunction(() => !document.body.innerText.includes('正在保存'));
  return saved.json();
}
async function cut(id, fraction) {
  const item = clip(id);
  await item.scrollIntoViewIfNeeded();
  const box = await item.boundingBox();
  return savedAction(() => item.click({ position: { x: box.width * fraction, y: box.height / 2 } }));
}
async function stable(expected, phase) {
  assert.deepEqual(shape(dub(await read())), expected, `${phase}: persisted arrangement changed`);
  for (const item of expected) {
    assert.equal(await clip(item.clip_id).count(), 1, `${phase}: missing or duplicate ${item.clip_id}`);
    assert.equal(await clip(item.clip_id).locator('..').getAttribute('data-dub-lane'), String(item.dub_lane ?? 0));
  }
}
try {
  const untouched = await page.request.patch(`${prefix}/timeline-edit`, { data: {
    schema_version: 'timeline-edit-patch-v2', added_clips: [{ clip_id: 'untouched',
      media_source_clip_id: 'old', start_ms: 4000, end_ms: 5000,
      source_start_ms: 0, source_end_ms: 1000, dub_lane: 2 }],
  } });
  assert.ok(untouched.ok(), await untouched.text());
  await page.goto(`${base}/video-localization?project_id=${project}`, { waitUntil: 'networkidle' });
  await clip('old').waitFor();
  const baseline = shape(dub(await read()));
  // Hold a preference receipt so edit + undo reach the save queue together.
  // The edit log is nonempty, but the resulting timeline is unchanged.
  let releasePreference;
  let preferenceCaptured;
  const preferenceReady = new Promise(resolve => { preferenceCaptured = resolve; });
  const preferenceRelease = new Promise(resolve => { releasePreference = resolve; });
  await page.route('**/video-localization/ui-state', async route => {
    const response = await route.fetch();
    preferenceCaptured();
    await preferenceRelease;
    await route.fulfill({ response });
  }, { times: 1 });
  await page.locator('button[aria-label$="鼠标预览"]').click();
  await preferenceReady;
  const initialBox = await clip('old').boundingBox();
  await page.mouse.move(initialBox.x + initialBox.width / 2, initialBox.y + initialBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(initialBox.x + initialBox.width / 2 + 30, initialBox.y + initialBox.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.waitForFunction(() => Number(document.querySelector('[data-audio-clip-id="old"]')?.getAttribute('data-render-start-ms')) > 0);
  await page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click();
  releasePreference();
  await page.waitForFunction(() => document.body.innerText.includes('已保存')
    && !document.body.innerText.includes('有未保存修改')
    && !document.body.innerText.includes('无法生成安全的局部保存请求'));
  await stable(baseline, 'edit and undo while save is queued');
  await savedAction(() => page.getByRole('button', { name: '重做时间线编辑', exact: true }).click());
  assert.notDeepEqual(shape(dub(await read())), baseline);
  await savedAction(() => page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click());
  await stable(baseline, 'redo remains usable after net-zero save');
  await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
  await cut('old', .33);
  await clip('old_part_2').waitFor();
  await cut('old_part_2', .5);
  await clip('old_part_2_part_2').waitFor();
  await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
  const beforeDelete = shape(dub(await read()));
  assert.equal(beforeDelete.length, baseline.length + 2);
  await clip('old_part_2').click();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-audio-clip-id')), 'old_part_2');
  await savedAction(() => page.keyboard.press('e'));
  const edited = shape(dub(await read()));
  assert.equal(edited.length, baseline.length + 1);
  assert.ok(!edited.some(item => item.clip_id === 'old_part_2'));
  assert.ok(edited.find(item => item.clip_id === 'old').end_ms < edited.find(item => item.clip_id === 'old_part_2_part_2').start_ms);
  assert.deepEqual(edited, beforeDelete.filter(item => item.clip_id !== 'old_part_2'));
  await stable(edited, 'cut and E delete');
  await savedAction(() => page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click());
  await stable(beforeDelete, 'undo deletion');
  await savedAction(() => page.getByRole('button', { name: '重做时间线编辑', exact: true }).click());
  await stable(edited, 'redo deletion');

  // Exercise a real revision-triggered poll and focus read after the cut.
  const ui = await page.request.patch(`${prefix}/ui-state`, { data: { inspector_tab: 'dubbing' } });
  assert.ok(ui.ok(), await ui.text());
  await page.waitForTimeout(5700);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForTimeout(300);
  await stable(edited, 'poll and focus');
  await page.reload({ waitUntil: 'networkidle' });
  await stable(edited, 'reload');
  assert.notEqual(dub(await read()).find(item => item.clip_id === 'old').end_ms,
    baseline.find(item => item.clip_id === 'old').end_ms);
  assert.deepEqual(edited.find(item => item.clip_id === 'untouched'), baseline.find(item => item.clip_id === 'untouched'));

  // ASR timing previously used the workspace endpoint, which rejects edits.
  const cue = page.locator('[data-track-id="subtitles"] [data-subtitle-item-id="cue"]');
  await cue.scrollIntoViewIfNeeded();
  await cue.click();
  const originalCue = (await read()).cues.find(item => item.cue_id === 'cue');
  const bounds = await cue.boundingBox();
  await savedAction(async () => {
    await page.mouse.move(bounds.x + bounds.width * .5, bounds.y + bounds.height * .5);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width * .5 + 25, bounds.y + bounds.height * .5, { steps: 5 });
    await page.mouse.up();
  });
  const movedCue = (await read()).cues.find(item => item.cue_id === 'cue');
  assert.notEqual(movedCue.start_ms, originalCue.start_ms);
  await savedAction(() => page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click());
  assert.equal((await read()).cues.find(item => item.cue_id === 'cue').start_ms, originalCue.start_ms);
  await savedAction(() => page.getByRole('button', { name: '重做时间线编辑', exact: true }).click());
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal((await read()).cues.find(item => item.cue_id === 'cue').start_ms, movedCue.start_ms);
  await stable(edited, 'subtitle save preserves audio cuts');

  // Dense zoomed-out timelines must retain selectable clips, not just a picture.
  const full = await (await page.request.get(prefix)).json();
  full.source_media.duration_ms = 600000;
  const extended = await page.request.put(prefix, { data: full });
  assert.ok(extended.ok(), await extended.text());
  const denseClips = Array.from({ length: 130 }, (_, index) => ({
    clip_id: `overview-${index}`, media_source_clip_id: 'old',
    start_ms: 10000 + index * 4000, end_ms: 11000 + index * 4000,
    source_start_ms: 0, source_end_ms: 1000, dub_lane: 0,
  }));
  const populated = await page.request.patch(`${prefix}/timeline-edit`, {
    data: { schema_version: 'timeline-edit-patch-v2', added_clips: denseClips },
  });
  assert.ok(populated.ok(), await populated.text());
  await page.reload({ waitUntil: 'networkidle' });
  const overview = page.locator('[data-track-id="dub"][data-dub-lane="0"] [data-timeline-overview-canvas="dub"]');
  await overview.waitFor();
  const overviewTarget = clip('overview-50');
  await overviewTarget.click();
  assert.equal(await overviewTarget.getAttribute('aria-pressed'), 'true');
  await savedAction(() => page.keyboard.press('e'));
  assert.ok(!dub(await read()).some(item => item.clip_id === 'overview-50'));
  await page.reload({ waitUntil: 'networkidle' });
  assert.ok(!dub(await read()).some(item => item.clip_id === 'overview-50'));
  assert.equal(dub(await read()).length, edited.length + 129);
  const uiSave = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/ui-state'));
  await page.locator('button[aria-label$="鼠标预览"]').click();
  const beforeUnloadPrevented = await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  assert.equal(beforeUnloadPrevented, false);
  assert.ok((await uiSave).ok());
  const visibleSelection = clip('overview-51');
  await visibleSelection.click();
  assert.equal(await visibleSelection.getAttribute('aria-pressed'), 'true');
  // Fine ASR-derived times must survive a click with sub-frame pointer jitter.
  const precise = await page.request.patch(`${prefix}/localized-subtitles/subtitle/edit`, {
    data: { start_ms: 1003, end_ms: 3013 },
  });
  assert.ok(precise.ok(), await precise.text());
  await page.reload({ waitUntil: 'networkidle' });
  const subtitle = page.locator('[data-subtitle-item-id="subtitle"]').last();
  await subtitle.scrollIntoViewIfNeeded();
  const subtitleBox = await subtitle.boundingBox();
  await page.mouse.move(subtitleBox.x + subtitleBox.width / 2, subtitleBox.y + subtitleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(subtitleBox.x + subtitleBox.width / 2 + 1, subtitleBox.y + subtitleBox.height / 2);
  await page.mouse.up();
  await page.waitForFunction(() => !document.body.innerText.includes('正在保存'));
  await page.reload({ waitUntil: 'networkidle' });
  const preserved = (await read()).localized_subtitles.find(item => item.subtitle_id === 'subtitle');
  assert.equal(preserved.start_ms, 1003);
  assert.equal(preserved.end_ms, 3013);
  await page.screenshot({ path: resolve(process.env.TIMELINE_EDIT_ARTIFACT_DIR, 'timeline-edited.png'), fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(consoleErrors, []);
  console.log(JSON.stringify({ timeline_editing: 'passed', checks: ['queued edit plus undo settles without error',
    'redo works after net-zero save', 'razor twice', 'E deletes middle',
    'undo and redo save', 'revision poll', 'focus', 'refresh', 'ASR timing persists', 'overview E deletion',
    'UI-only change has no unload prompt', 'sub-frame subtitle timing survives refresh', 'no extra tracks or restored parent', 'no browser errors'] }));
} catch (error) {
  console.error(error.stack);
  console.error(JSON.stringify({ clips: shape(dub(await read())), pageErrors: errors, consoleErrors,
    saveGuardVisible: await page.getByText('时间线编辑无法生成安全的局部保存请求，已保留修改，请重试', { exact: true }).count() }));
  await page.screenshot({ path: resolve(process.env.TIMELINE_EDIT_ARTIFACT_DIR, 'timeline-edit-failed.png'), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
