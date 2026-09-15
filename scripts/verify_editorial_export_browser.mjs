import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { browserAudioOptions, browserAudioEvidence } from './browser_audio_options.mjs';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const base = process.env.TIMELINE_EDIT_URL;
const project = process.env.TIMELINE_EDIT_PROJECT;
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base)
  && ![5173, 18000, 65335].includes(Number(new URL(base).port)) && project);
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
browserAudioEvidence();
const browser = await chromium.launch(isolatedBrowserOptions({ ...(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
  ? { channel: 'chrome' } : {}), ...browserAudioOptions() }));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
const prefix = `${base}/api/projects/${project}/video-localization`;
const read = async () => (await (await page.request.get(`${prefix}/workspace`)).json()).draft;
try {
  const initial = await read();
  const old = initial.timeline_clips.find(clip => clip.clip_id === 'old');
  assert.deepEqual(
    initial.dub_subtitles.map(caption => [caption.subtitle_id, caption.needs_review]),
    [['dub-old', false], ['dub-unchanged', false]]
  );
  const expected = Object.fromEntries(['start_ms', 'end_ms', 'source_start_ms', 'source_end_ms',
    'media_source_clip_id', 'dub_lane'].map(key => [key, old[key] ?? null]));
  const seeded = await page.request.patch(`${prefix}/timeline-edit`, { data: {
    clip_patches: [{ clip_id: 'old', expected_generation_identity: old.generation_identity,
      expected_editable_fields: expected, start_ms: 250, end_ms: 1291 }],
    added_clips: [{ clip_id: 'short-window', media_source_clip_id: 'old',
      start_ms: 2000, end_ms: 2500, source_start_ms: 0, source_end_ms: 800, dub_lane: 0 },
      { clip_id: 'short-source', media_source_clip_id: 'old',
        start_ms: 3000, end_ms: 4041, source_start_ms: 0, source_end_ms: 500, dub_lane: 0 }],
  } });
  assert.ok(seeded.ok(), await seeded.text());
  const before = (await read()).timeline_clips;
  await page.goto(`${base}/video-localization?project_id=${project}`, { waitUntil: 'networkidle' });
  await page.locator('[data-audio-clip-id="old"]').waitFor();
  await page.locator('video.preview-video').evaluate(video => { video.currentTime = 3.1; });
  await page.waitForFunction(() => {
    const video = document.querySelector('video.preview-video');
    return video && !video.seeking && video.readyState >= 2;
  });
  await page.locator('video.preview-video').evaluate(video => video.play());
  await page.waitForFunction(() => {
    const audio = document.querySelector('audio[data-dub-clip="short-source"]');
    return audio && !audio.paused;
  });
  await page.waitForFunction(() => {
    const video = document.querySelector('video.preview-video');
    const audio = document.querySelector('audio[data-dub-clip="short-source"]');
    return video && video.currentTime >= 3.65 && (!audio || audio.paused);
  });
  await page.locator('video.preview-video').evaluate(video => video.pause());
  await page.getByRole('button', { name: '导出', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出成品' });
  await dialog.getByRole('button', { name: '音频', exact: true }).click();
  for (const label of ['原音轨', '人声轨', '背景音乐']) {
    const row = dialog.getByRole('button', { name: new RegExp(label) });
    if (await row.getAttribute('aria-pressed') === 'true') await row.click();
  }
  const dub = dialog.getByRole('button', { name: /合成配音/ });
  if (await dub.getAttribute('aria-pressed') !== 'true') await dub.click();
  await dialog.getByLabel('文件名称').fill('editorial-export.wav');
  const submitted = page.waitForResponse(response => response.request().method() === 'POST'
    && response.url().endsWith('/export/render'));
  await dialog.getByRole('button', { name: '导出音频', exact: true }).click();
  const response = await submitted;
  assert.ok(response.ok(), await response.text());
  const operation = await response.json();
  assert.ok(['queued', 'running', 'success'].includes(operation.status));
  await dialog.getByText('导出完成', { exact: true }).waitFor({ timeout: 30000 });
  assert.equal(await dialog.locator('[role="alert"]').count(), 0);
  assert.deepEqual((await read()).timeline_clips, before);
  await dialog.getByRole('button', { name: '关闭导出面板' }).click();
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('[data-audio-clip-id="old"]').waitFor();
  const reloaded = await read();
  assert.deepEqual(reloaded.timeline_clips, before);
  assert.deepEqual(
    reloaded.dub_subtitles.map(caption => [caption.subtitle_id, caption.needs_review]),
    [['dub-old', true], ['dub-unchanged', false]]
  );
  const detail = await (await page.request.get(`${prefix}/operations/${operation.operation_id}`)).json();
  assert.equal(detail.status, 'success');
  assert.equal(detail.result_summary.filename, 'editorial-export.wav');
  // The history panel reads the same real operation after reload.
  const history = page.locator('.task-row.history-row').filter({ hasText: '导出' });
  await history.first().waitFor();
  assert.equal(await history.first().evaluate(node => node.classList.contains('failed')), false);
  await history.first().locator('.history-summary').click();
  await history.first().locator('.history-details').waitFor();
  // The Agent's public execution entry must preserve manually split material.
  const snapshotResponse = await page.request.get(`${prefix}/dubbing/snapshot`);
  assert.ok(snapshotResponse.ok(), await snapshotResponse.text());
  const snapshot = await snapshotResponse.json();
  const planned = await page.request.post(`${prefix}/dubbing/plan`, { data: {
    source_revision: snapshot.source_revision, semantic_units: snapshot.semantic_units,
    boundaries: snapshot.boundaries, policy: { preferred_group_units: 1 },
  } });
  assert.ok(planned.ok(), await planned.text());
  const runResponse = await page.request.get(`${prefix}/dubbing/production-run`);
  assert.ok(runResponse.ok(), await runResponse.text());
  const run = await runResponse.json();
  const manual = run.groups.find(group => group.existing_timeline_clip_ids.includes('old'));
  assert.ok(manual?.timeline_requires_reconciliation);
  const preservedClips = (await read()).timeline_clips;
  const execute = await page.request.post(`${prefix}/dubbing/production-run/execute`, { data: {
    scope: 'single_group', group_id: manual.group_id,
  } });
  assert.ok(execute.ok(), await execute.text());
  assert.equal((await execute.json()).status, 'needs_attention');
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('[data-audio-clip-id="old"]').waitFor();
  assert.deepEqual((await read()).timeline_clips, preservedClips);
  const completionResponse = await page.request.get(`${prefix}/dubbing/completion`);
  assert.ok(completionResponse.ok(), await completionResponse.text());
  const completion = await completionResponse.json();
  assert.equal(completion.schema_version, 'dubbing-completion-v1');
  assert.equal(completion.status, 'incomplete');
  assert.ok(completion.invalid_clip_ids.includes('short-source'));
  assert.deepEqual((await read()).timeline_clips, preservedClips);
  const readinessResponse = await page.request.get(`${prefix}/readiness`);
  assert.ok(readinessResponse.ok(), await readinessResponse.text());
  const readiness = await readinessResponse.json();
  assert.deepEqual(readiness.dubbing_completion, completion);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ editorial_export: 'success', operation_persisted: true,
    edits_unchanged: true, browser_errors: errors.length }));
} finally {
  await browser.close();
}
