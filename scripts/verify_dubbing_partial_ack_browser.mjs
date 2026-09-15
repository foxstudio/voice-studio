#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.DUBBING_PARTIAL_ACK_URL;
const project = process.env.DUBBING_PARTIAL_ACK_PROJECT;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000].includes(Number(new URL(base).port)) || !project) {
  throw new Error('Only an isolated acceptance project is allowed');
}
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const prefix = `${base}/api/projects/${project}/video-localization`;
const url = `${base}/video-localization?project_id=${project}`;
const expectedEditableFields = clip => ({
  start_ms: clip.start_ms ?? null,
  end_ms: clip.end_ms ?? null,
  source_start_ms: clip.source_start_ms ?? null,
  source_end_ms: clip.source_end_ms ?? null,
  media_source_clip_id: clip.media_source_clip_id ?? null,
  dub_lane: clip.dub_lane ?? null,
});
const trimOldClip = async (clipId = 'old') => {
  const clip = page.locator(`[data-audio-clip-id="${clipId}"]`);
  await clip.scrollIntoViewIfNeeded();
  await clip.click();
  const bounds = await clip.boundingBox();
  const handle = await clip.locator('.clip-handle.end').boundingBox();
  assert.ok(bounds && handle, 'A real timeline trim handle is required');
  const handleX = Math.min(handle.x + handle.width / 2, bounds.x + bounds.width - 0.5);
  await page.mouse.move(handleX, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleX - bounds.width * 0.2, handle.y + handle.height / 2, { steps: 3 });
  await page.mouse.up();
};
const renderedClipEndAtTimelineStart = async clipId => {
  await page.getByRole('region', { name: '音频与字幕轨道滚动区域' }).evaluate(element => {
    element.scrollLeft = 0;
    element.dispatchEvent(new Event('scroll'));
  });
  const clip = page.locator(`[data-audio-clip-id="${clipId}"]`);
  await clip.waitFor({ state: 'attached', timeout: 30000 });
  const renderedEnd = await clip.getAttribute('data-render-end-ms');
  assert.notEqual(renderedEnd, null, 'The detailed timeline clip must expose its rendered end');
  return Number(renderedEnd);
};
try {
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.locator('[data-audio-clip-id="old"]').waitFor({ state: 'attached' });
  await page.locator('video.preview-video').evaluate(video => {
    if (video.readyState >= HTMLMediaElement.HAVE_METADATA) return;
    return new Promise((resolve, reject) => {
      video.addEventListener('loadedmetadata', resolve, { once: true });
      video.addEventListener('error', () => reject(new Error('preview metadata failed')), { once: true });
    });
  });
  assert.equal(await page.locator('audio[aria-label="背景音乐轨预览"]').count(), 0,
    'Paused idle preview must leave the background audio element unmounted');
  await page.waitForFunction(() => ![...document.querySelectorAll('.preview-cache-strip [aria-label]')]
    .some(node => node.getAttribute('aria-label')?.includes('缓存中')));
  console.log(JSON.stringify({ scenario: 'paused-idle-cache-status', result: 'passed',
    checks: ['video metadata ready', 'background audio unmounted', 'no false caching range'] }));
  let idleContentWrites = 0;
  const recordIdleSave = request => {
    if ((request.method() === 'PUT' && [prefix, `${prefix}/workspace`].includes(request.url()))
      || (request.method() === 'PATCH' && request.url() === `${prefix}/timeline-edit`)) idleContentWrites += 1;
  };
  page.on('request', recordIdleSave);
  await page.keyboard.press('Control+s');
  await page.getByText('已手动保存', { exact: true }).waitFor({ state: 'visible' });
  await page.keyboard.press('Meta+s');
  await page.waitForTimeout(200);
  page.off('request', recordIdleSave);
  assert.equal(idleContentWrites, 0, 'An idle manual save must not rewrite the full workspace or materialize untouched tracks');
  console.log(JSON.stringify({ scenario: 'idle-manual-save', result: 'passed', checks: ['Ctrl+S', 'Meta+S', 'no content rewrite'] }));
  const beforeSystemEdit = await (await page.request.get(prefix)).json();
  assert.ok(!beforeSystemEdit.timeline_clips.some(clip => clip.clip_id === 'media_vocals'));
  await trimOldClip('media_vocals');
  await trimOldClip();
  const systemSaved = page.waitForResponse(response => response.url() === `${prefix}/timeline-edit`
    && response.request().method() === 'PATCH');
  await page.keyboard.press('Control+s');
  const systemResponse = await systemSaved;
  assert.ok(systemResponse.ok(), await systemResponse.text());
  await page.getByText('已保存', { exact: false }).first().waitFor({ state: 'visible' });
  const savedSystem = (await (await page.request.get(prefix)).json()).timeline_clips;
  const savedVocals = savedSystem.find(clip => clip.clip_id === 'media_vocals');
  assert.ok(savedVocals?.end_ms < 6000, 'The visible system track must support its first real edit');
  assert.ok(savedSystem.find(clip => clip.clip_id === 'old').end_ms < 1000,
    'The system-track edit must not block the dubbing edit in the same save');
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(Number(await page.locator('[data-audio-clip-id="media_vocals"]').getAttribute('data-render-end-ms')), savedVocals.end_ms);
  console.log(JSON.stringify({ scenario: 'first-system-track-save', result: 'passed',
    checks: ['read projection not persisted', 'real vocals trim plus dub trim', 'Ctrl+S commits together', 'reload persisted'] }));
  const conflictOriginal = (await (await page.request.get(prefix)).json()).timeline_clips.find(clip => clip.clip_id === 'old');
  const conflictRevision = await (await page.request.get(`${prefix}/workspace-revision`)).json();
  const conflictRevisionRoute = `${prefix}/workspace-revision`;
  await page.route(conflictRevisionRoute, route => route.fulfill({ json: conflictRevision }));
  await trimOldClip();
  const retainedLocalEnd = Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms'));
  const authoritativeEnd = conflictOriginal.end_ms - 50;
  const authoritativeSourceEnd = conflictOriginal.source_end_ms - 50;
  const newerEdit = await page.request.patch(`${prefix}/timeline-edit`, { data: {
    schema_version: 'timeline-edit-patch-v2',
    clip_patches: [{
      clip_id: 'old',
      expected_generation_identity: conflictOriginal.generation_identity || conflictOriginal.task_id || 'old',
      expected_editable_fields: expectedEditableFields(conflictOriginal),
      end_ms: authoritativeEnd,
      source_end_ms: authoritativeSourceEnd,
    }],
  } });
  assert.ok(newerEdit.ok(), await newerEdit.text());
  const rejectedOldEdit = await page.waitForResponse(response =>
    response.url() === `${prefix}/timeline-edit`
      && response.request().method() === 'PATCH'
      && response.status() === 409
  );
  const rejectedOldEditBody = await rejectedOldEdit.json();
  assert.equal(rejectedOldEditBody.error.code, 'VIDEO_LOCALIZATION_TIMELINE_CLIP_CHANGED');
  assert.equal(rejectedOldEditBody.error.detail.editable, true);
  await page.locator('[role="alert"]').waitFor({ state: 'visible' });
  assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), retainedLocalEnd,
    'A rejected stale trim must remain visible for retry instead of disappearing');
  assert.ok(Math.abs(retainedLocalEnd - authoritativeEnd) > 40,
    'The retained local trim and newer server edit must stay distinguishable');
  const conflictPersisted = await (await page.request.get(prefix)).json();
  assert.equal(conflictPersisted.timeline_clips.find(clip => clip.clip_id === 'old').end_ms, authoritativeEnd,
    'The stale browser trim must not overwrite the newer same-media edit');
  // Ctrl+S uses the same pending edits and must retain the actionable server error.
  const manualConflict = page.waitForResponse(response =>
    response.url() === `${prefix}/timeline-edit`
      && response.request().method() === 'PATCH'
      && response.status() === 409
  );
  await page.keyboard.press('Control+s');
  const manualConflictBody = await (await manualConflict).json();
  assert.equal(manualConflictBody.error.code, rejectedOldEditBody.error.code);
  await page.waitForFunction(message => [...document.querySelectorAll('[role="alert"]')]
    .some(node => node.textContent.includes(message)), manualConflictBody.error.message);
  assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), retainedLocalEnd,
    'Manual retry must retain the unsaved trim without replacing the server result');
  await page.unroute(conflictRevisionRoute);
  await page.reload({ waitUntil: 'networkidle' });
  assert.ok(Math.abs(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')) - authoritativeEnd) <= 40,
    'The newer same-media edit must survive reload');
  console.log(JSON.stringify({ scenario: 'same-media-stale-edit-fence', result: 'passed',
    checks: ['newer edit committed', 'old edit rejected', 'local edit retained with visible failure',
      'server edit not overwritten', 'explicit reload shows authoritative state'] }));

  // A subtitle mutation requested while a compact timeline acknowledgement is
  // delayed must wait behind that save owner. Releasing the acknowledgement
  // lets both writes finish once, without a stale retry or full-workspace PUT.
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.locator('[data-audio-clip-id="old"]').waitFor({ state: 'attached' });
  const raceOriginal = (await (await page.request.get(prefix)).json()).timeline_clips.find(clip => clip.clip_id === 'old');
  const timelineEditUrl = `${prefix}/timeline-edit`;
  let releaseFirstTimelineResponse;
  const responseRelease = new Promise(resolve => { releaseFirstTimelineResponse = resolve; });
  let firstUpstreamAck;
  let signalFirstCommit;
  const firstCommit = new Promise(resolve => { signalFirstCommit = resolve; });
  let interceptedTimelineWrites = 0;
  let workspacePuts = 0;
  let subtitleRequestStarted = false;
  const recordRaceRequest = request => {
    if (request.url() === prefix && request.method() === 'PUT') workspacePuts += 1;
    if (request.url() === `${prefix}/localized-subtitles/subtitle/edit`
      && request.method() === 'PATCH') subtitleRequestStarted = true;
  };
  page.on('request', recordRaceRequest);
  await page.route(timelineEditUrl, async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    interceptedTimelineWrites += 1;
    if (interceptedTimelineWrites !== 1) return route.continue();
    const upstream = await route.fetch();
    assert.ok(upstream.ok(), await upstream.text());
    firstUpstreamAck = await upstream.json();
    signalFirstCommit();
    await responseRelease;
    await route.fulfill({ response: upstream });
  });
  await trimOldClip();
  await firstCommit;
  await page.keyboard.press('Control+s');
  await page.keyboard.press('Control+s');
  await page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]').click();
  await page.getByRole('button', { name: '字幕', exact: true }).click();
  const subtitleEditor = page.locator('textarea.subtitle-textarea').first();
  await subtitleEditor.waitFor({ state: 'visible' });
  const subtitleText = `${await subtitleEditor.inputValue()}（并发保存）`;
  const subtitleSaved = page.waitForResponse(response =>
    response.url() === `${prefix}/localized-subtitles/subtitle/edit`
      && response.request().method() === 'PATCH'
  );
  await subtitleEditor.fill(subtitleText);
  await page.waitForTimeout(500);
  assert.equal(subtitleRequestStarted, false,
    'The subtitle mutation must wait for the in-flight compact timeline acknowledgement');
  releaseFirstTimelineResponse();
  assert.ok((await subtitleSaved).ok(), 'The queued subtitle mutation must succeed after the timeline acknowledgement');
  await page.waitForTimeout(300);
  const racePersisted = await (await page.request.get(prefix)).json();
  const raceClip = racePersisted.timeline_clips.find(clip => clip.clip_id === 'old');
  assert.ok(raceClip.end_ms < raceOriginal.end_ms && raceClip.source_end_ms < raceOriginal.source_end_ms,
    'The trim committed before response invalidation must remain persisted');
  assert.equal(racePersisted.localized_subtitles.find(item => item.subtitle_id === 'subtitle').text, subtitleText,
    'The concurrent mutation must remain persisted');
  assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), raceClip.end_ms,
    'Conflict refresh must show the authoritative committed trim');
  assert.equal(interceptedTimelineWrites, 1, 'The committed compact timeline write must not be retried');
  assert.equal(workspacePuts, 0, 'The serialized saves must not fall back to a full-workspace PUT');
  assert.ok(firstUpstreamAck?.timeline_clips?.some(clip => clip.clip_id === 'old'));
  page.off('request', recordRaceRequest);
  await page.unroute(timelineEditUrl);
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), raceClip.end_ms,
    'Committed trim must survive reload after the invalidated response race');
  console.log(JSON.stringify({ scenario: 'serialized-editorial-saves', result: 'passed',
    checks: ['server commit before delayed response', 'subtitle request waits in shared save queue',
      'one compact write', 'one subtitle write', 'no retry or full workspace save', 'reload persisted'] }));

  // Simulate a committed write whose HTTP response never reaches the browser.
  // The next Ctrl+S must first replay the same transaction, then the newer trim.
  const lostBaseline = await (await page.request.get(`${prefix}/workspace-revision`)).json();
  await page.route(conflictRevisionRoute, route => route.fulfill({ json: lostBaseline }));
  const lostRequests = [];
  let firstCommittedReceipt;
  await page.route(timelineEditUrl, async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    lostRequests.push(route.request().postDataJSON());
    const upstream = await route.fetch();
    assert.ok(upstream.ok(), await upstream.text());
    if (lostRequests.length === 1) {
      firstCommittedReceipt = await upstream.json();
      return route.abort('connectionreset');
    }
    return route.fulfill({ response: upstream });
  });
  await trimOldClip();
  await page.keyboard.press('Control+s');
  await page.getByText('保存失败', { exact: false }).first().waitFor({ state: 'visible' });
  assert.ok(firstCommittedReceipt, 'The failed response must follow a real server commit');
  assert.equal(lostRequests.length, 1, 'Do not spin on an uncertain save');
  await trimOldClip();
  const lastTrimEnd = Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms'));
  await page.keyboard.press('Meta+s');
  await page.getByText('已保存', { exact: false }).first().waitFor({ state: 'visible' });
  assert.equal(lostRequests.length, 3, 'Retry the uncertain packet once, then send the newer edit');
  assert.deepEqual(lostRequests[1], lostRequests[0], 'Retry must retain both request ID and exact payload');
  assert.notEqual(lostRequests[2].request_id, lostRequests[0].request_id);
  const lostPersisted = await (await page.request.get(prefix)).json();
  assert.equal(lostPersisted.timeline_clips.find(clip => clip.clip_id === 'old').end_ms, lastTrimEnd);
  await page.unroute(timelineEditUrl);
  await page.unroute(conflictRevisionRoute);
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), lastTrimEnd);
  console.log(JSON.stringify({ scenario: 'lost-save-response-retry', result: 'passed',
    checks: ['real commit before connection reset', 'no automatic retry loop', 'same transaction replay',
      'newer trim saved separately', 'reload persisted'] }));

  // An idempotent replay returns the original partial receipt. If this browser
  // has already consumed a newer complete workspace, that old receipt confirms
  // the checkpoint but must not overwrite the newer baseline.
  const staleReplayBaseline = await (await page.request.get(`${prefix}/workspace-revision`)).json();
  let holdStaleReplayRevision = true;
  await page.route(conflictRevisionRoute, async route => {
    if (holdStaleReplayRevision) return route.fulfill({ json: staleReplayBaseline });
    return route.continue();
  });
  const staleReplayRequests = [];
  let staleReplayFirstReceipt;
  await page.route(timelineEditUrl, async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    staleReplayRequests.push(route.request().postDataJSON());
    const upstream = await route.fetch();
    assert.ok(upstream.ok(), await upstream.text());
    if (staleReplayRequests.length === 1) {
      staleReplayFirstReceipt = await upstream.json();
      return route.abort('connectionreset');
    }
    return route.fulfill({ response: upstream });
  });
  await trimOldClip();
  await page.keyboard.press('Control+s');
  await page.getByText('保存失败', { exact: false }).first().waitFor({ state: 'visible' });
  assert.ok(staleReplayFirstReceipt, 'The first compact edit must commit before its response is lost');
  const afterStaleReplayCommit = await (await page.request.get(prefix)).json();
  const staleReplayClip = afterStaleReplayCommit.timeline_clips.find(clip => clip.clip_id === 'old');
  const remoteEnd = Math.max(staleReplayClip.start_ms + 1, staleReplayClip.end_ms - 40);
  const remoteSourceEnd = Math.max(staleReplayClip.source_start_ms + 1, staleReplayClip.source_end_ms - 40);
  const remoteClipId = 'remote-after-lost-receipt';
  const remoteWrite = await page.request.patch(timelineEditUrl, { data: {
    schema_version: 'timeline-edit-patch-v2',
    clip_patches: [{
      clip_id: 'old',
      expected_generation_identity: staleReplayClip.generation_identity || staleReplayClip.task_id || 'old',
      expected_editable_fields: expectedEditableFields(staleReplayClip),
      end_ms: remoteEnd,
      source_end_ms: remoteSourceEnd,
    }],
    added_clips: [{ clip_id: remoteClipId, media_source_clip_id: 'old', start_ms: 7000,
      end_ms: 7100, source_start_ms: 0, source_end_ms: 100, dub_lane: 0 }],
  } });
  assert.ok(remoteWrite.ok(), await remoteWrite.text());
  const remotePersisted = await (await page.request.get(prefix)).json();
  const remotePersistedClip = remotePersisted.timeline_clips.find(clip => clip.clip_id === 'old');
  assert.equal(remotePersistedClip.end_ms, remoteEnd,
    'The authoritative R2 edit must persist its exact raw timeline milliseconds');
  assert.equal(remotePersistedClip.source_end_ms, remoteSourceEnd,
    'The authoritative R2 edit must persist its exact raw source milliseconds');
  // This fixture starts both ranges at zero and retains equal source/timeline
  // lengths. Assert those premises, then independently round to its 30 fps grid.
  assert.equal(remotePersisted.source_media.frame_rate, 30);
  assert.equal(remotePersistedClip.start_ms, 0);
  assert.equal(remotePersistedClip.source_start_ms, 0);
  assert.equal(remoteSourceEnd, remoteEnd);
  const expectedRenderedRemoteEnd = Math.round(Math.round(remoteEnd * 30 / 1000) * 1000 / 30);
  holdStaleReplayRevision = false;
  await page.waitForTimeout(1100);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.locator(`[data-audio-clip-id="${remoteClipId}"]`).waitFor({ state: 'attached' });
  const staleReplayResponse = page.waitForResponse(response => response.url() === timelineEditUrl
    && response.request().method() === 'PATCH');
  await page.keyboard.press('Meta+s');
  assert.ok((await staleReplayResponse).ok());
  await page.getByText('已保存', { exact: false }).first().waitFor({ state: 'visible' });
  assert.equal(staleReplayRequests.length, 2, 'The old compact packet must be replayed exactly once');
  assert.deepEqual(staleReplayRequests[1], staleReplayRequests[0],
    'The confirming replay must preserve the original request ID and payload');
  assert.equal(await renderedClipEndAtTimelineStart('old'), expectedRenderedRemoteEnd,
    'The old replay receipt must not replace the newer consumed clip state after frame projection');
  await page.unroute(timelineEditUrl);
  await page.unroute(conflictRevisionRoute);
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await renderedClipEndAtTimelineStart('old'), expectedRenderedRemoteEnd);
  await page.locator(`[data-audio-clip-id="${remoteClipId}"]`).waitFor({ state: 'attached' });
  console.log(JSON.stringify({ scenario: 'stale-lost-response-receipt', result: 'passed',
    checks: ['R1 response lost', 'R2 complete workspace consumed', 'R1 replay only confirms checkpoint',
      'R2 remains visible', 'reload persisted'] }));

  for (const scenario of [
    { name: 'ui-state', button: '静音人声轨', path: '/ui-state', start: 2000 },
    { name: 'timeline-edit', path: '/timeline-edit', start: 4000 },
    { name: 'timeline-delete', action: 'delete', path: '/timeline-edit', start: 6000 },
  ]) {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.locator('[data-audio-clip-id="old"]').waitFor({ state: 'attached' });
    const originalClip = (await (await page.request.get(prefix)).json()).timeline_clips.find(clip => clip.clip_id === 'old');
    const baseline = await (await page.request.get(`${prefix}/workspace-revision`)).json();
    let holdRevision = true;
    let releasedPolls = 0;
    const revisionRoute = `${prefix}/workspace-revision`;
    await page.route(revisionRoute, async route => {
      if (holdRevision) await route.fulfill({ json: baseline });
      else { releasedPolls += 1; await route.continue(); }
    });
    const clipId = `remote-${scenario.name}`;
    // Another writer adds existing media through the public editing contract.
    // No generation/feed event can independently refresh the tested browser.
    const added = await page.request.patch(`${prefix}/timeline-edit`, { data: {
      added_clips: [{ clip_id: clipId, media_source_clip_id: 'old', start_ms: scenario.start,
        end_ms: scenario.start + 1000, source_start_ms: 0, source_end_ms: 1000, dub_lane: 0 }],
    } });
    assert.ok(added.ok(), await added.text());
    assert.equal(await page.locator(`[data-audio-clip-id="${clipId}"]`).count(), 0,
      'The other writer must be hidden until polling is released');
    const button = scenario.button ? page.getByRole('button', { name: scenario.button, exact: true }) : null;
    const previous = button ? await button.getAttribute('aria-pressed') : null;
    const saved = page.waitForResponse(response => response.url() === prefix + scenario.path
      && response.request().method() === 'PATCH');
    if (button) await button.click();
    else if (scenario.action === 'delete') {
      await page.locator('[data-audio-clip-id="old"]').click();
      await page.getByRole('button', { name: '删除', exact: true }).click();
    }
    else {
      await trimOldClip();
    }
    const response = await saved;
    assert.ok(response.ok(), await response.text());
    if (scenario.action === 'delete') {
      const request = response.request().postDataJSON();
      assert.deepEqual(request.deleted_clips, [{
        clip_id: 'old',
        expected_generation_identity: originalClip.generation_identity || originalClip.task_id || 'old',
        expected_editable_fields: expectedEditableFields(originalClip),
      }], 'Deletion must use the typed clip identity fence');
      assert.ok(request.ui_state_patch, 'Deletion-related timeline state must share the typed transaction');
      assert.ok(Object.hasOwn(request.ui_state_patch, 'discarded_tts_task_ids'),
        'Deletion must carry its discarded-task state in the same request');
    }
    const ack = await response.json();
    assert.notEqual(ack.revision, baseline.revision);
    assert.ok(!ack.timeline_clips?.some(clip => clip.clip_id === clipId),
      'This must be a partial ack, not a complete snapshot containing the remote clip');
    // Let the page consume the real partial ack before the next revision read.
    await page.waitForTimeout(100);
    holdRevision = false;
    let visible = false;
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      visible = await page.locator(`[data-audio-clip-id="${clipId}"]`).count() === 1;
      if (visible && releasedPolls >= 1) break;
      await page.waitForTimeout(100);
    }
    const persisted = await (await page.request.get(prefix)).json();
    assert.ok(persisted.timeline_clips.some(clip => clip.clip_id === clipId), 'Remote clip is persisted');
    assert.ok(releasedPolls >= 1, 'A fresh revision poll must have run');
    assert.ok(visible, `${scenario.name}: partial save ack swallowed remote clip despite ${releasedPolls} fresh polls`);
    const ownClip = persisted.timeline_clips.find(clip => clip.clip_id === 'old');
    if (scenario.action === 'delete') assert.equal(ownClip, undefined, 'Deleted clip must stay absent');
    else if (!button) assert.ok(ownClip.end_ms < originalClip.end_ms && ownClip.source_end_ms < originalClip.source_end_ms,
      'Real trim must persist');
    const assertOwnEdit = async () => {
      if (button) assert.equal(await button.getAttribute('aria-pressed'), previous === 'true' ? 'false' : 'true', 'Own mute edit survives');
      else if (scenario.action === 'delete') assert.equal(await page.locator('[data-audio-clip-id="old"]').count(), 0,
        'Deleted clip stays hidden');
      else assert.equal(Number(await page.locator('[data-audio-clip-id="old"]').getAttribute('data-render-end-ms')), ownClip.end_ms, 'Own trim survives');
    };
    await assertOwnEdit();
    await page.unroute(revisionRoute);
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator(`[data-audio-clip-id="${clipId}"]`).waitFor({ state: 'attached' });
    await assertOwnEdit();
    await page.screenshot({ path: resolve(process.env.DUBBING_PARTIAL_ACK_ARTIFACT_DIR, `${scenario.name}.png`), fullPage: true });
    console.log(JSON.stringify({ scenario: scenario.name, result: 'passed', releasedPolls,
      checks: ['remote public API edit', 'real UI partial save', 'new clip without reload', 'own edit retained', 'reload persisted'] }));
  }
  assert.deepEqual(errors, [], 'No browser runtime errors');
} catch (error) {
  await page.screenshot({ path: resolve(process.env.DUBBING_PARTIAL_ACK_ARTIFACT_DIR, 'failure.png'), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
