#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
// Invoked only by the temporary-root content harness, never a user service.
import assert from 'node:assert/strict';
import { browserAudioOptions, browserAudioEvidence } from './browser_audio_options.mjs';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.DUBBING_CONTENT_E2E_URL;
const project = process.env.DUBBING_CONTENT_E2E_PROJECT;
const badResult = process.env.DUBBING_CONTENT_E2E_BAD_RESULT;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000].includes(Number(new URL(base).port)) || !project) {
  throw new Error('An isolated non-production project URL is required');
}
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
browserAudioEvidence();
const browser = await chromium.launch(isolatedBrowserOptions({ ...(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}), ...browserAudioOptions() }));
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
const projectUrl = `${base}/video-localization?project_id=${project}`;

async function checkPlaybackPauseRetention() {
  await page.getByRole('button', { name: '跳到视频开始', exact: true }).click();
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await page.waitForFunction(() => {
    const video = document.querySelector('video');
    const audio = document.querySelector('audio[data-dub-clip="old"]');
    return video && !video.paused && audio && audio.readyState >= 2;
  });
  const audio = await page.locator('audio[data-dub-clip="old"]').elementHandle();
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  assert.ok(await audio.evaluate(node => node.isConnected && node.paused),
    'Pause must keep the decoded nearby audio element mounted and silent');
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await page.waitForFunction(() => {
    const video = document.querySelector('video');
    return Boolean(video && !video.paused);
  });
  assert.ok(await audio.evaluate(node => node === document.querySelector('audio[data-dub-clip="old"]')),
    'Resume must reuse the same audio element');
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await page.getByRole('button', { name: '跳到视频开始', exact: true }).click();
  await audio.dispose();
  console.log(JSON.stringify({ playbackPauseRetention: 'passed' }));
}

async function checkGeneratedClipMove() {
  await page.setViewportSize({ width: 1440, height: 1800 });
  const draftUrl = `${base}/api/projects/${project}/video-localization`;
  const before = await (await page.request.get(draftUrl)).json();
  const clip = before.timeline_clips.find(item => item.result_id && item.clip_id !== 'raw-history-import');
  assert.ok(clip, 'A newly generated clip must be tested, not just the old fixture');
  const selector = `[data-audio-clip-id="${clip.clip_id}"]`;
  const element = page.locator(selector);
  await element.scrollIntoViewIfNeeded();
  await element.locator('[data-waveform-resolution="detail"]').waitFor();
  await element.click();
  const original = await element.elementHandle();
  await page.evaluate(selector => {
    const frames = [];
    let running = true;
    const sample = () => {
      const clip = document.querySelector(selector);
      const canvas = clip?.querySelector('canvas');
      frames.push({ visible: Boolean(canvas && canvas.width > 0 && canvas.getBoundingClientRect().width > 0),
        exists: Boolean(clip), lane: clip?.parentElement?.dataset.dubLane,
        resolution: clip?.querySelector('.clip-waveform')?.dataset.waveformResolution,
        width: canvas?.getBoundingClientRect().width, clipWidth: clip?.getBoundingClientRect().width,
        x: clip?.getBoundingClientRect().x, start: clip?.dataset.renderStartMs, end: clip?.dataset.renderEndMs,
        viewport: document.querySelector('.track-canvas')?.getBoundingClientRect().toJSON() });
      if (running) requestAnimationFrame(sample);
    };
    window.__moveFrameAudit = { frames, stop: () => { running = false; } };
    requestAnimationFrame(sample);
  }, selector);
  try {
    const box = await element.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 24, box.y + box.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.waitForFunction(({ selector, start }) => Number(document.querySelector(selector)?.dataset.renderStartMs) !== start,
      { selector, start: clip.start_ms });
    assert.ok(await original.evaluate(node => node.isConnected), 'Same-lane movement must not remount a generated clip');
    const frames = await page.evaluate(() => window.__moveFrameAudit.frames);
    assert.ok(frames.length > 0 && frames.every(frame => frame.visible), 'Generated clip canvas must remain visible at every sampled paint');
    await page.waitForFunction(async ({ url, id, start }) => {
      const draft = await (await fetch(url)).json();
      return draft.timeline_clips.find(item => item.clip_id === id)?.start_ms !== start;
    }, { url: draftUrl, id: clip.clip_id, start: clip.start_ms });
    const saved = (await (await page.request.get(draftUrl)).json()).timeline_clips.find(item => item.clip_id === clip.clip_id);
    assert.equal(saved.source_start_ms, clip.source_start_ms);
    assert.equal(saved.source_end_ms, clip.source_end_ms);
    assert.equal(saved.result_id, clip.result_id);
    const lane = page.locator('[data-track-row][data-track-id="dub"][data-dub-lane="0"]');
    const target = await lane.boundingBox();
    const source = await element.boundingBox();
    // Four seconds is empty on lane zero in this owned six-second fixture.
    const targetX = target.x + target.width * (4000 + (clip.end_ms - clip.start_ms) / 2) / 6000;
    await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
    await page.mouse.down();
    await page.mouse.move(targetX, target.y + target.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.waitForFunction(({ selector }) => document.querySelector(selector)?.parentElement?.dataset.dubLane === '0', { selector });
    await page.waitForFunction(async ({ url, id }) => {
      const draft = await (await fetch(url)).json();
      return draft.timeline_clips.find(item => item.clip_id === id)?.dub_lane === 0;
    }, { url: draftUrl, id: clip.clip_id });
    const laneFrames = await page.evaluate(() => window.__moveFrameAudit.frames);
    assert.ok(laneFrames.every(frame => frame.visible), `Cached waveform must paint without a blank frame after cross-lane remount: ${JSON.stringify(laneFrames.filter(frame => !frame.visible))}`);
    console.log(JSON.stringify({ generatedClipMove: 'passed', sameLanePaintFrames: frames.length,
      crossLanePaintFrames: laneFrames.length, pathRedactedReceipt: 'source retained' }));
  } finally {
    await page.evaluate(() => { window.__moveFrameAudit.stop(); delete window.__moveFrameAudit; });
    await original.dispose();
  }
  await page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click();
  await page.waitForFunction(async ({ url, id, lane }) => {
    const draft = await (await fetch(url)).json();
    return draft.timeline_clips.find(item => item.clip_id === id)?.dub_lane === lane;
  }, { url: draftUrl, id: clip.clip_id, lane: clip.dub_lane });
  await page.getByRole('button', { name: '撤销时间线编辑', exact: true }).click();
  await page.waitForFunction(async ({ url, id, start }) => {
    const draft = await (await fetch(url)).json();
    return draft.timeline_clips.find(item => item.clip_id === id)?.start_ms === start;
  }, { url: draftUrl, id: clip.clip_id, start: clip.start_ms });
  await page.reload({ waitUntil: 'networkidle' });
}

async function checkTimelineTrimWaveform(clipId) {
  await page.setViewportSize({ width: 1440, height: 1800 });
  const draftUrl = `${base}/api/projects/${project}/video-localization`;
  const before = await (await page.request.get(draftUrl)).json();
  const good = before.tts_tasks.find(task => task.stages[1].status === 'success');
  const clip = before.timeline_clips.find(item => clipId ? item.clip_id === clipId : item.result_id === good.result_id);
  const selector = `[data-audio-clip-id="${clip.clip_id}"]`;
  const element = page.locator(selector);
  await element.scrollIntoViewIfNeeded();
  for (let count = 0; count < 5; count++) await page.getByRole('button', { name: '放大时间线', exact: true }).click();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.getByRole('region', { name: '音频与字幕轨道滚动区域' }).evaluate((node, fraction) => {
    node.scrollTo({ left: Math.max(0, node.scrollWidth * fraction - node.clientWidth / 3) });
  }, clip.start_ms / before.source_media.duration_ms);
  await element.scrollIntoViewIfNeeded();
  await element.locator('[data-waveform-resolution="detail"]').waitFor();
  await element.click();
  await page.waitForFunction(selector => document.querySelector(selector)?.classList.contains('selected'), selector);
  const audioUrl = `${base}/api/projects/${project}/video-localization/timeline-clips/${clip.clip_id}/audio`;
  const digest = async () => createHash('sha256').update(await (await page.request.get(audioUrl)).body()).digest('hex');
  const originalDigest = await digest();
  await element.locator('.clip-waveform').evaluate(node => {
    node.waveformTransitions = [];
    node.waveformObserver = new MutationObserver(changes => {
      for (const change of changes) if (change.attributeName === 'data-waveform-resolution') node.waveformTransitions.push(node.getAttribute('data-waveform-resolution'));
    });
    node.waveformObserver.observe(node, { attributes: true });
  });
  let release;
  const pendingDetail = new Promise(resolvePending => { release = resolvePending; });
  let requested = false;
  const pattern = `**/timeline-clips/${clip.media_source_clip_id || clip.clip_id}/waveform?*`;
  const handler = async route => {
    const parameters = new URL(route.request().url()).searchParams;
    if (parameters.has('end_ms') && Number(parameters.get('end_ms')) < Number(clip.source_end_ms)) {
      requested = true;
      await pendingDetail;
    }
    await route.continue();
  };
  await page.route(pattern, handler);
  try {
    const bounds = await element.boundingBox();
    const handle = await element.locator('.clip-handle.end').boundingBox();
    assert.ok(handle.y >= 0 && handle.y + handle.height <= page.viewportSize().height, `Trim handle must be on screen: ${JSON.stringify(handle)}`);
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle.x + handle.width / 2 - bounds.width * 0.2, handle.y + handle.height / 2, { steps: 3 });
    await page.mouse.up();
    await page.waitForFunction(({ selector, original }) => Number(document.querySelector(selector)?.dataset.renderEndMs) < original,
      { selector, original: clip.end_ms });
    await element.locator('.clip-waveform:not(.refining)[data-waveform-resolution="detail"]').waitFor();
    assert.equal(await element.locator('.clip-waveform').getAttribute('data-waveform-resolution'), 'detail', 'Trimming must not replace covered detail with cached coarse preview');
    const transitions = await element.locator('.clip-waveform').evaluate(node => node.waveformTransitions);
    assert.deepEqual(transitions.filter(state => state !== 'detail'), [], 'No intermediate preview/blank waveform during trim');
    await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, 'timeline-trim-retained-detail.png'), fullPage: true });
    release();
    await element.locator('.clip-waveform:not(.refining)[data-waveform-resolution="detail"]').waitFor();
    let trimmed;
    for (let attempt = 0; attempt < 150; attempt++) {
      const draft = await (await page.request.get(draftUrl)).json();
      trimmed = draft.timeline_clips.find(item => item.clip_id === clip.clip_id);
      if (trimmed?.end_ms < clip.end_ms) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(trimmed.source_end_ms < clip.source_end_ms && trimmed.end_ms < clip.end_ms,
      JSON.stringify({ originalEnd: clip.end_ms, savedEnd: trimmed.end_ms, originalSourceEnd: clip.source_end_ms, savedSourceEnd: trimmed.source_end_ms }));
    assert.equal(trimmed.source_start_ms, clip.source_start_ms);
    assert.equal(trimmed.result_id, clip.result_id);
    assert.equal(requested, false, 'Covered trim must reuse cached detail without a new source-window request');
    assert.equal(await digest(), originalDigest, 'Trim must retain the exact source audio bytes');
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator(selector).locator('[data-waveform-resolution="detail"]').waitFor();
    assert.equal(Number(await page.locator(selector).getAttribute('data-render-end-ms')), trimmed.end_ms);
    await checkTimelineSplitWaveform(trimmed, originalDigest);
    console.log(JSON.stringify({ timelineTrim: 'passed', originalEndMs: clip.source_end_ms,
      trimmedEndMs: trimmed.source_end_ms, checks: ['real drag trim', 'covered detail reused without a request', 'same audio bytes', 'refresh persisted'] }));
  } finally {
    release();
    await page.unroute(pattern, handler);
  }
}

async function checkRawHistoryDrag() {
  await page.setViewportSize({ width: 1440, height: 1800 });
  const draftUrl = `${base}/api/projects/${project}/video-localization`;
  const before = await (await page.request.get(draftUrl)).json();
  await page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]').click();
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^全部片段/ }).click();
  const waveform = page.locator(`.tts-history .history-row[data-result-id="${badResult}"] .wave-track`);
  await waveform.scrollIntoViewIfNeeded();
  const from = await waveform.boundingBox();
  const lane = await page.locator('[data-track-id="dub"][data-dub-lane="0"]').boundingBox();
  assert.ok(from && lane && lane.y > 0 && lane.y + lane.height < page.viewportSize().height);
  const placed = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname.endsWith(`/timeline-clips/history/${badResult}/apply`));
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(lane.x + lane.width * 0.65, lane.y + lane.height / 2, { steps: 12 });
  await page.getByLabel('配音落位预览', { exact: true }).waitFor();
  await page.mouse.up();
  const response = await placed;
  assert.ok(response.ok(), await response.text());
  assert.equal(response.request().postDataJSON().force_new, true);
  const clipId = response.request().postDataJSON().new_clip_id;
  const after = await (await page.request.get(draftUrl)).json();
  assert.equal(after.timeline_clips.length, before.timeline_clips.length + 1);
  for (const clip of before.timeline_clips) {
    assert.deepEqual(after.timeline_clips.find(item => item.clip_id === clip.clip_id), clip, 'History drag preserves every existing clip');
  }
  const imported = after.timeline_clips.find(item => item.clip_id === clipId);
  assert.equal(imported.result_id, badResult);
  await page.reload({ waitUntil: 'networkidle' });
  const element = page.locator(`[data-audio-clip-id="${clipId}"]`);
  await element.scrollIntoViewIfNeeded();
  await element.click();
  await page.waitForFunction(id => document.querySelector(`[data-audio-clip-id="${id}"]`)?.classList.contains('selected'), clipId);
  const bounds = await element.boundingBox();
  const handle = await element.locator('.clip-handle.start').boundingBox();
  const saved = page.waitForResponse(result => result.request().method() === 'PATCH'
    && new URL(result.url()).pathname.endsWith('/timeline-edit'));
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + bounds.width * 0.25, handle.y + handle.height / 2, { steps: 5 });
  await page.mouse.up();
  assert.ok((await saved).ok());
  const trimmed = (await (await page.request.get(draftUrl)).json()).timeline_clips.find(item => item.clip_id === clipId);
  assert.ok(trimmed.source_start_ms > imported.source_start_ms, 'Imported raw material must allow cutting its unwanted prefix');
  assert.equal(trimmed.source_end_ms, imported.source_end_ms);
  await page.reload({ waitUntil: 'networkidle' });
  await element.waitFor();
  assert.deepEqual((await (await page.request.get(draftUrl)).json()).timeline_clips.find(item => item.clip_id === clipId), trimmed);
  assert.equal((await (await page.request.get(`${base}/api/__content_acceptance/state`)).json()).asr_calls, 0,
    'Dragging and editing raw history must not invoke content recognition');
  console.log(JSON.stringify({ rawHistoryDrag: 'passed', clipId, sourceStartMs: trimmed.source_start_ms,
    checks: ['raw history pointer drag', 'old clips preserved', 'unwanted prefix editable', 'refresh persisted', 'no raw ASR gate'] }));
  return clipId;
}

async function checkTimelineSplitWaveform(clip, originalDigest) {
  const selector = `[data-audio-clip-id="${clip.clip_id}"]`;
  const childSelector = `[data-audio-clip-id="${clip.clip_id}_part_2"]`;
  const element = page.locator(selector);
  const child = page.locator(childSelector);
  let release;
  const pendingDetail = new Promise(resolve => { release = resolve; });
  const pattern = `**/timeline-clips/${clip.media_source_clip_id || clip.clip_id}/waveform?*`;
  const handler = async route => {
    if (new URL(route.request().url()).searchParams.has('start_ms')) await pendingDetail;
    await route.continue();
  };
  await page.route(pattern, handler);
  try {
    await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
    const box = await element.boundingBox();
    await element.click({ position: { x: box.width / 2, y: box.height / 2 } });
    await child.waitFor();
    await child.locator('.clip-waveform:not(.refining)[data-waveform-resolution="detail"]').waitFor();
    assert.equal(await child.locator('.clip-waveform').getAttribute('data-waveform-resolution'), 'detail', 'A new split child must reuse sufficient parent source detail, not coarse preview');
    assert.equal(await element.locator('.clip-waveform').getAttribute('data-waveform-resolution'), 'detail');
    await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, 'timeline-split-retained-detail.png'), fullPage: true });
    release();
    await child.locator('.clip-waveform:not(.refining)[data-waveform-resolution="detail"]').waitFor();
    if (await page.getByRole('button', { name: '剃刀工具', exact: true }).getAttribute('aria-pressed') === 'true') {
      await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
    }
    let pieces = [];
    for (let attempt = 0; attempt < 150; attempt++) {
      const draft = await (await page.request.get(`${base}/api/projects/${project}/video-localization`)).json();
      pieces = draft.timeline_clips.filter(item => [clip.clip_id, `${clip.clip_id}_part_2`].includes(item.clip_id)).sort((a, b) => a.start_ms - b.start_ms);
      if (pieces.length === 2) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.equal(pieces.length, 2);
    assert.equal(pieces[0].source_start_ms, clip.source_start_ms);
    assert.equal(pieces[0].source_end_ms, pieces[1].source_start_ms);
    assert.equal(pieces[1].source_end_ms, clip.source_end_ms);
    assert.equal(pieces[0].end_ms, pieces[1].start_ms);
    for (const piece of pieces) {
      const response = await page.request.get(`${base}/api/projects/${project}/video-localization/timeline-clips/${piece.clip_id}/audio`);
      assert.equal(createHash('sha256').update(await response.body()).digest('hex'), originalDigest);
    }
    await page.reload({ waitUntil: 'networkidle' });
    await child.locator('[data-waveform-resolution="detail"]').waitFor();
    console.log(JSON.stringify({ timelineSplit: 'passed', pieces: pieces.map(piece => ({ id: piece.clip_id, sourceStart: piece.source_start_ms, sourceEnd: piece.source_end_ms })),
      checks: ['real razor', 'new child immediate detailed source coverage', 'same audio bytes and continuous source mapping', 'refresh persisted'] }));
  } finally {
    release();
    await page.unroute(pattern, handler);
  }
}

async function checkResponsiveWaveform() {
  const requests = [];
  const payloads = new Map();
  let release;
  const ready = new Promise(resolveReady => { release = resolveReady; });
  const routePattern = '**/api/history/*/waveform';
  const handler = async route => {
    requests.push(route.request().url());
    const response = await route.fetch();
    payloads.set(route.request().url(), await response.json());
    await ready;
    await route.fulfill({ response });
  };
  await page.route(routePattern, handler);
  const selectHistory = async () => {
    await page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]').click();
    await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
    await page.getByRole('tab', { name: /^当前片段/ }).click();
    await page.locator('.tts-history .audio-waveform').first().scrollIntoViewIfNeeded();
  };
  const waveform = page.locator('.tts-history .audio-waveform').first();
  const snapshot = async () => waveform.evaluate(shell => {
    const track = shell.querySelector('.wave-track');
    const heights = Array.from(shell.querySelectorAll('.wave-bars-base span'), bar => parseFloat(bar.style.getPropertyValue('--bar-height')));
    return { width: track.clientWidth, count: heights.length, heights, marker: shell.dataset.acceptanceIdentity };
  });
  try {
    await page.setViewportSize({ width: 1087, height: 1100 });
    await selectHistory();
    await waveform.locator('.wave-button[aria-busy="true"]').waitFor();
    assert.equal((await snapshot()).count, 0, 'Unloaded audio must not show fabricated waveform bars');
    release();
    await waveform.locator('.wave-button[aria-busy="false"]').waitFor();
    const sourceUrl = new URL(await waveform.locator('.download-button').getAttribute('href'), base).href.replace(/\/audio$/, '/waveform');
    const payload = payloads.get(sourceUrl);
    assert.ok(payload?.peaks.length > 0 && new Set(payload.peaks).size > 8, 'Real fixture waveform response must carry a changing envelope');
    assert.ok(Math.max(...payload.peaks) < 0.15, 'Quiet fixture must exercise display normalization rather than only loud audio');
    await waveform.evaluate(shell => { shell.dataset.acceptanceIdentity = 'same-mounted-waveform'; });
    const requestCount = requests.filter(url => url === sourceUrl).length;
    assert.equal(requestCount, 1);
    const results = [];
    for (const width of [1087, 768, 390, 1440, 1087]) {
      await page.setViewportSize({ width, height: 1100 });
      await waveform.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => {
        const shell = document.querySelector('[data-acceptance-identity="same-mounted-waveform"]');
        const track = shell?.querySelector('.wave-track');
        return track && shell.querySelectorAll('.wave-bars-base span').length === Math.max(1, Math.min(320, Math.floor((track.clientWidth - 10) / 3)));
      });
      const result = await snapshot();
      assert.equal(result.marker, 'same-mounted-waveform', 'Resize must preserve the same player component');
      assert.ok(new Set(result.heights.map(height => height.toFixed(1))).size > 8, 'Loaded waveform must not be uniform placeholder bars');
      assert.ok(result.heights.at(-1) > result.heights[0] + 25, 'Rendered waveform must follow the real rising fixture envelope');
      assert.equal(requests.filter(url => url === sourceUrl).length, requestCount, 'Resize must not refetch waveform data');
      results.push({ viewport: width, track: result.width, bars: result.count });
    }
    assert.ok(results[0].bars > results[2].bars * 2, 'Wide and narrow players must have meaningfully different bar density');
    await waveform.getByRole('button', { name: '播放配音', exact: true }).click();
    await waveform.getByRole('button', { name: '暂停配音', exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('[data-acceptance-identity="same-mounted-waveform"] audio')?.currentTime > 0.05);
    await waveform.getByRole('button', { name: '暂停配音', exact: true }).click();
    assert.ok(await waveform.locator('audio').evaluate(audio => audio.paused));
    const track = waveform.locator('.wave-track');
    const bounds = await track.boundingBox();
    await track.click({ position: { x: bounds.width * 0.4, y: bounds.height / 2 } });
    await page.waitForFunction(() => parseFloat(document.querySelector('[data-acceptance-identity="same-mounted-waveform"] .wave-track').style.getPropertyValue('--progress')) >= 35);
    await waveform.getByRole('button', { name: '暂停配音', exact: true }).click();
    await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, 'dubbing-responsive-waveform.png'), fullPage: true });
    await page.reload({ waitUntil: 'networkidle' });
    await selectHistory();
    await waveform.locator('.wave-button[aria-busy="false"]').waitFor();
    assert.ok((await snapshot()).count > 50, 'Waveform must reload visibly after page refresh');
    await waveform.getByRole('button', { name: '播放配音', exact: true }).click();
    await waveform.getByRole('button', { name: '暂停配音', exact: true }).waitFor();
    await waveform.getByRole('button', { name: '暂停配音', exact: true }).click();
    console.log(JSON.stringify({ waveform: 'passed', results, resizeRequests: requestCount,
      checks: ['no pending placeholder', 'actual envelope', 'same component', 'resize no refetch', 'play pause seek', 'refresh replay'] }));
  } finally {
    release();
    await page.unroute(routePattern, handler);
  }
}

async function checkDubbingResponsive() {
  const longId = 'empty_' + 'long_target_identifier_'.repeat(8);
  const results = [];
  const inspect = async (width, label) => {
    const workspace = page.locator('.dubbing-workspace');
    await workspace.waitFor();
    await workspace.scrollIntoViewIfNeeded();
    const layout = await page.locator('aside.inspector').evaluate(aside => {
      const tabs = aside.querySelector('.inspector-mode-tabs').getBoundingClientRect();
      const panel = aside.querySelector('.dubbing-workspace').getBoundingClientRect();
      return { available: tabs.width, panel: panel.width, aside: aside.getBoundingClientRect().width,
        overflow: aside.scrollWidth - aside.clientWidth,
        panelOverflow: aside.querySelector('.dubbing-workspace').scrollWidth - aside.querySelector('.dubbing-workspace').clientWidth };
    });
    await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, `dubbing-${width}-${label}.png`), fullPage: true });
    assert.ok(layout.panel >= layout.available - 3, `${width} ${label}: half-width panel ${JSON.stringify(layout)}`);
    assert.ok(layout.panel <= layout.available + 3, `${width} ${label}: exceeds inspector width ${JSON.stringify(layout)}`);
    assert.ok(layout.overflow <= 2 && layout.panelOverflow <= 2, `${width} ${label}: horizontal overflow ${JSON.stringify(layout)}`);
    if (width === 1440) assert.ok(layout.aside >= 320 && layout.aside <= 562, `desktop sidebar width ${layout.aside}`);
    results.push({ width, label, ...layout });
  };
  for (const width of [1087, 768, 390, 1440]) {
    await page.setViewportSize({ width, height: 1100 });
    await page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]').click();
    await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
    const current = page.getByRole('tab', { name: /^当前片段/ });
    const all = page.getByRole('tab', { name: /^全部片段/ });
    await current.click();
    await page.locator('.tts-history .reuse-button').first().waitFor();
    await inspect(width, 'history');
    for (const selector of ['.parameter-trigger', '.reuse-button', '.apply-button', '.delete-record']) {
      const button = page.locator(`.tts-history ${selector}`).first();
      await button.scrollIntoViewIfNeeded();
      const buttonBox = await button.boundingBox();
      const panelBox = await page.locator('.dubbing-workspace').boundingBox();
      assert.ok(buttonBox && buttonBox.width > 0 && buttonBox.x >= panelBox.x - 1 && buttonBox.x + buttonBox.width <= panelBox.x + panelBox.width + 1,
        `${width}: ${selector} must remain visible within panel`);
    }
    await all.click();
    assert.equal(await all.getAttribute('aria-selected'), 'true');
    if (width === 1087) {
      const wave = page.locator('.history-list .wave-track').first();
      await wave.scrollIntoViewIfNeeded();
      await wave.hover();
      const owner = await wave.evaluateHandle((element) => {
        for (let node = element.parentElement; node; node = node.parentElement) {
          if (/auto|scroll/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight + 2) return node;
        }
        return null;
      });
      const before = await owner.evaluate(node => {
        if (!node) throw new Error('History must have a scrollable ancestor');
        return node.scrollTop;
      });
      const delta = before > 100 ? -100 : 100;
      await page.mouse.wheel(0, delta);
      await page.waitForFunction(({node, before}) => Math.abs(node.scrollTop - before) > 90, {node: owner, before});
      await page.mouse.wheel(0, -delta);
      await page.waitForFunction(({node, before}) => Math.abs(node.scrollTop - before) < 3, {node: owner, before});
      await owner.dispose();
      console.log(JSON.stringify({historyWaveformWheel: 'passed', directions: ['up', 'down'], width}));
    }
    await current.click();
    assert.equal(await current.getAttribute('aria-selected'), 'true');
    await page.locator(`[data-track-id="localizedSubtitles"] [data-subtitle-item-id="${longId}"]`).click();
    await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
    await current.click();
    await page.locator('.tts-history .history-empty').waitFor();
    assert.match(await page.locator('.target-summary').innerText(), /long_target_identifier/);
    await inspect(width, 'empty-long-title');
    await all.click();
    await page.locator('.tts-history .reuse-button').first().waitFor();
    await current.click();
    await page.locator('.tts-history .history-empty').waitFor();
    if (width === 1440) {
      for (const [target, delta] of [[320, 140], [560, -360]]) {
        const handle = page.getByRole('button', { name: '调整侧边栏宽度', exact: true });
        await handle.scrollIntoViewIfNeeded();
        const bounds = await handle.boundingBox();
        await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
        await page.mouse.down();
        await page.mouse.move(bounds.x + bounds.width / 2 + delta, bounds.y + bounds.height / 2, { steps: 6 });
        await page.mouse.up();
        const aside = await page.locator('aside.inspector').boundingBox();
        assert.ok(Math.abs(aside.width - target) <= 2, `desktop resize to ${target}: ${aside.width}`);
        await inspect(width, `sidebar-${target}-empty-long-title`);
        await all.click();
        await page.locator('.tts-history .reuse-button').first().waitFor();
        await inspect(width, `sidebar-${target}-all-history`);
        await current.click();
      }
    }
  }
  console.log(JSON.stringify({ responsive: 'passed', results }));
}

try {
  await page.goto(projectUrl, { waitUntil: 'networkidle' });
  await checkPlaybackPauseRetention();
  await checkGeneratedClipMove();
  if (process.env.DUBBING_CONTENT_E2E_TRIM_ONLY === '1') {
    await checkTimelineTrimWaveform();
  } else {
  await page.locator('.task-row.history-row:not(.failed)').first().waitFor();
  assert.equal(await page.locator('.task-row.history-row.failed').count(), 0);
  const response = await page.request.get(`${base}/api/projects/${project}/video-localization`);
  const draft = await response.json();
  const bad = draft.tts_tasks.find(task => task.result_id === badResult);
  const good = draft.tts_tasks.find(task => task.result_id !== badResult);
  assert.equal(bad.stages[0].status, 'success');
  assert.equal(bad.stages[1].status, 'success');
  assert.equal(draft.timeline_clips.some(clip => clip.result_id === bad.result_id), true);
  assert.equal(draft.timeline_clips.some(clip => clip.result_id === good.result_id), true);
  await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, 'content-refresh.png') });
  if (process.env.DUBBING_CONTENT_E2E_RESPONSIVE === '1') {
    await checkResponsiveWaveform();
    await checkDubbingResponsive();
  }
  await page.reload({ waitUntil: 'networkidle' });
  const importedClipId = await checkRawHistoryDrag();
  await checkTimelineTrimWaveform(importedClipId);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', browser: process.env.DUBBING_CONTENT_E2E_TRIM_ONLY === '1'
    ? ['timeline drag trim', 'razor split', 'source audio retained', 'refresh persistence']
    : ['both generated materials appended', 'history drag', 'prefix trim', 'old clips preserved', 'refresh persistence', 'no raw ASR gate'],
    trigger: 'public handoff and generate APIs; real SPA history pointer drag, trim and split' }));
} catch (error) {
  console.error(error.stack);
  await page.screenshot({ path: resolve(process.env.DUBBING_CONTENT_E2E_ARTIFACT_DIR, 'content-failure.png'), fullPage: true });
  console.error((await page.locator('body').innerText()).slice(-12000));
  throw error;
} finally {
  await page.context().request.dispose();
  await page.context().close();
  await browser.close();
}
