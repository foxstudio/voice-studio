#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.TIMELINE_VIEWPORT_URL;
const project = process.env.TIMELINE_VIEWPORT_PROJECT;
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base) && ![5173, 18000, 65335].includes(Number(new URL(base).port)) && project);
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions({
  ...(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}),
  ignoreDefaultArgs: ['--hide-scrollbars'],
}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.setDefaultTimeout(15000);
const canvas = page.getByRole('region', { name: '音频与字幕轨道滚动区域' });
const target = page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]');
let timelineSpanMs = 6000;
const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const snapshot = () => canvas.evaluate((node, durationMs) => {
  const content = node.querySelector('.timeline-content');
  const cue = node.querySelector('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]');
  const box = node.getBoundingClientRect();
  const cueBox = cue?.getBoundingClientRect();
  const main = document.querySelector('.main');
  return { width: node.clientWidth, scroll: node.scrollLeft, total: node.scrollWidth,
    contentWidth: content.getBoundingClientRect().width, timelineSpanMs: durationMs,
    cachedWidth: Number.parseFloat(content.style.getPropertyValue('--processing-width')),
    startMs: node.scrollLeft / content.getBoundingClientRect().width * durationMs,
    cueX: cueBox ? cueBox.left - box.left : null,
    mainOverflow: main.scrollHeight > main.clientHeight,
    mainScrollbarWidth: main.offsetWidth - main.clientWidth };
}, timelineSpanMs);
async function checkNarrowSelectionScrollbar() {
  await page.setViewportSize({ width: 1087, height: 600 });
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '任务', exact: true }).click();
  await settle();
  for (let attempt = 0; attempt < 6; attempt++) {
    const overflow = await page.locator('.main').evaluate(node => node.scrollHeight - node.clientHeight);
    if (overflow <= 0) break;
    console.log(JSON.stringify({ findingTaskFit: { attempt, height: page.viewportSize().height, overflow } }));
    await page.setViewportSize({ width: 1087, height: page.viewportSize().height + overflow + 12 });
    await settle();
  }
  await canvas.scrollIntoViewIfNeeded();
  const before = await snapshot();
  assert.equal(before.mainOverflow, false, 'The task panel must fit before subtitle selection');
  await target.click({ position: { x: 25, y: 16 } });
  await page.locator('.tts-history .history-row').first().waitFor();
  await settle();
  const after = await snapshot();
  console.log(JSON.stringify({ narrowSelectionBefore: before, narrowSelectionAfter: after, viewport: page.viewportSize() }));
  assert.equal(after.mainOverflow, true, 'Opening real dubbing history must create vertical overflow');
  assert.ok(after.width !== before.width || after.mainScrollbarWidth === 0,
    'A non-overlay scrollbar must change the timeline width');
  assert.equal(after.cachedWidth, after.width);
  assert.ok(Math.abs(after.startMs - before.startMs) < 0.2, `Subtitle click moved the timeline when the scrollbar appeared: ${JSON.stringify({ before, after })}`);
  assert.match(await page.locator('.target-summary').innerText(), /风格变了角色也变了/);
  await page.screenshot({ path: resolve(process.env.TIMELINE_VIEWPORT_ARTIFACT_DIR, 'timeline-selection-scrollbar.png'), fullPage: true });
}
try {
  await page.goto(`${base}/video-localization?project_id=${project}`, { waitUntil: 'networkidle' });
  const workspace = await (await page.request.get(`${base}/api/projects/${project}/video-localization/workspace`)).json();
  const draft = workspace.draft;
  timelineSpanMs = Math.max(draft.source_media.duration_ms, ...draft.cues.map(cue => cue.end_ms),
    ...draft.localized_subtitles.map(cue => cue.end_ms), ...draft.timeline_clips.map(clip => clip.end_ms));
  console.log(JSON.stringify({ sourceDuration: draft.source_media.duration_ms, timelineSpanMs }));
  await target.waitFor();
  await canvas.scrollIntoViewIfNeeded();
  const initial = await snapshot();
  assert.ok(initial.scroll > 10000, JSON.stringify(initial));
  await target.click({ position: { x: 25, y: 16 } });
  await settle();
  const selected = await snapshot();
  assert.ok(Math.abs(selected.startMs - initial.startMs) < 0.2, `Selection changed viewport time: ${JSON.stringify({ initial, selected })}`);
  assert.ok(selected.cueX !== null, 'Selected cue must remain rendered');
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^全部片段/ }).click();
  await settle();
  const history = await snapshot();
  assert.ok(Math.abs(history.startMs - selected.startMs) < 0.2, `History selection changed viewport time: ${JSON.stringify({ selected, history })}`);
  await checkNarrowSelectionScrollbar();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await settle();
  const beforeResize = await snapshot();
  await page.setViewportSize({ width: 1425, height: 1100 });
  await settle();
  const resized = await snapshot();
  console.log(JSON.stringify({ beforeResize, afterResize: resized }));
  assert.notEqual(resized.width, beforeResize.width, 'Resize must actually change the timeline canvas width');
  assert.equal(resized.cachedWidth, resized.width, 'Resize must update the viewport used for virtualization');
  assert.ok(Math.abs(resized.startMs - beforeResize.startMs) < 0.2, `Resize changed the visible time anchor: ${JSON.stringify({ beforeResize, resized })}`);
  await canvas.evaluate(node => node.scrollBy({ left: 120 }));
  await settle();
  const scrolled = await snapshot();
  assert.ok(scrolled.startMs > resized.startMs, 'Explicit horizontal scrolling must still move the viewport');
  await page.getByRole('button', { name: '放大时间线', exact: true }).click();
  await settle();
  const zoomed = await snapshot();
  assert.ok(zoomed.total > scrolled.total, 'Zoom must still change the timeline scale');
  await page.waitForFunction(({ projectId, expectedStartMs, expectedZoom }) => {
    const saved = JSON.parse(sessionStorage.getItem(`voice-studio-video-localization-view:${projectId}`) ?? 'null');
    return saved?.timeline_viewport_start_ms === expectedStartMs && saved?.timeline_zoom === expectedZoom;
  }, { projectId: project, expectedStartMs: Math.round(zoomed.startMs), expectedZoom: zoomed.contentWidth / zoomed.width });
  const settledZoom = await snapshot();
  const savedView = await page.evaluate(id => JSON.parse(sessionStorage.getItem(`voice-studio-video-localization-view:${id}`)), project);
  await page.reload({ waitUntil: 'networkidle' });
  await canvas.waitFor();
  await settle();
  const refreshed = await snapshot();
  console.log(JSON.stringify({ zoomed, settledZoom, savedView, refreshed }));
  assert.ok(Math.abs(refreshed.startMs - settledZoom.startMs) < 1.1, `Refresh lost the saved time anchor: ${JSON.stringify({ zoomed, settledZoom, savedView, refreshed })}`);
  assert.equal(refreshed.cachedWidth, refreshed.width);
  await canvas.evaluate(node => node.scrollTo({ left: node.scrollWidth }));
  await settle();
  const atEnd = await snapshot();
  console.log(JSON.stringify({ scrolledToLogicalEnd: atEnd }));
  assert.ok(atEnd.scroll + atEnd.width <= atEnd.contentWidth + 1,
    `Native scrolling entered label overflow beyond the timeline: ${JSON.stringify(atEnd)}`);
  await page.waitForFunction(({ projectId, expectedStartMs }) => {
    const saved = JSON.parse(sessionStorage.getItem(`voice-studio-video-localization-view:${projectId}`) ?? 'null');
    return saved?.timeline_viewport_start_ms === expectedStartMs;
  }, { projectId: project, expectedStartMs: Math.round(atEnd.startMs) });
  await page.reload({ waitUntil: 'networkidle' });
  await canvas.waitFor();
  await settle();
  const endRefreshed = await snapshot();
  assert.ok(Math.abs(endRefreshed.startMs - atEnd.startMs) < 1.1,
    `Refresh moved the end viewport: ${JSON.stringify({ atEnd, endRefreshed })}`);
  assert.ok(endRefreshed.scroll + endRefreshed.width <= endRefreshed.contentWidth + 1);
  // Fractional CSS widths must not accumulate hundreds of pixels of drift at high zoom.
  await page.evaluate(id => {
    const key = `voice-studio-video-localization-view:${id}`;
    const saved = JSON.parse(sessionStorage.getItem(key) ?? '{}');
    sessionStorage.setItem(key, JSON.stringify({ ...saved, timeline_zoom: 788, timeline_viewport_start_ms: 2500 }));
  }, project);
  await page.reload({ waitUntil: 'networkidle' });
  for (const fractionalWidth of [670.5, 670.25]) {
    await page.addStyleTag({ content: `.track-canvas { width: ${fractionalWidth}px !important; min-width: ${fractionalWidth}px !important; max-width: ${fractionalWidth}px !important; }` });
    await settle();
    const fractional = await snapshot();
    assert.ok(Math.abs(fractional.contentWidth - fractional.cachedWidth * 788) < 1,
      `Fractional viewport changed the rendered time scale: ${JSON.stringify(fractional)}`);
    const sourceWave = page.locator('[data-audio-clip-id="raw-history-import"] canvas');
    await sourceWave.waitFor({ state: 'visible' });
    const waveformEdges = await sourceWave.evaluate(node => {
      const viewport = document.querySelector('.track-canvas').getBoundingClientRect();
      const wave = node.getBoundingClientRect();
      return { leftGap: wave.left - viewport.left, rightGap: viewport.right - wave.right };
    });
    assert.ok(Math.abs(waveformEdges.leftGap) < 2 && Math.abs(waveformEdges.rightGap) < 2,
      `Waveform does not cover both viewport edges: ${JSON.stringify(waveformEdges)}`);
    console.log(JSON.stringify({ fractionalViewport: fractional, waveformEdges }));
  }
  assert.deepEqual(errors, []);
  await page.screenshot({ path: resolve(process.env.TIMELINE_VIEWPORT_ARTIFACT_DIR, 'timeline-viewport.png'), fullPage: true });
  console.log(JSON.stringify({ timelineViewport: 'passed', checks: ['high zoom late cue selection', 'history panel', 'resize time anchor', 'virtualization width', 'horizontal scroll', 'zoom', 'refresh', 'native end scroll clamp and refresh'] }));
} catch (error) {
  console.error(error.stack);
  console.error(JSON.stringify(await snapshot()));
  await page.screenshot({ path: resolve(process.env.TIMELINE_VIEWPORT_ARTIFACT_DIR, 'timeline-viewport-failed.png'), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
