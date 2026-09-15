#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { browserAudioOptions, browserAudioEvidence } from './browser_audio_options.mjs';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { verifyTimelineControls } from './verify_timeline_controls_browser.mjs';

const base = process.env.DUBBING_APPEND_URL;
const expected = JSON.parse(process.env.DUBBING_APPEND_EXPECTED ?? '{}');
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000].includes(Number(new URL(base).port)) || !expected.project_id) {
  throw new Error('Only an isolated acceptance project is allowed');
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

async function verifyTrackIdentity(phase) {
  const rows = await page.locator('.track-row[data-audio-selection-track]').evaluateAll(elements => elements.map(row => {
    const track = row.dataset.trackId;
    const lane = Number(row.dataset.dubLane ?? 0);
    const label = track === 'dub'
      ? document.querySelector(`.track-label[aria-label="合成配音轨 ${lane + 1}"]`)
      : document.querySelector(`.track-label.track-${track}`);
    return {
      track, lane, aligned: Boolean(label && Math.abs(label.getBoundingClientRect().top - row.getBoundingClientRect().top) < 2),
      ids: Array.from(row.querySelectorAll('[data-audio-clip-id]'), clip => clip.dataset.audioClipId)
    };
  }));
  assert.ok(rows.length, `${phase}: timeline rows are missing`);
  for (const row of rows) assert.ok(row.aligned, `${phase}: ${row.track}/${row.lane} header does not match its canvas`);
  const ids = rows.flatMap(row => row.ids);
  assert.equal(new Set(ids).size, ids.length, `${phase}: duplicate rendered clip identity`);
  assert.deepEqual(errors, [], `${phase}: rendering errors`);
}

async function verifyHistoryPlacementReplay() {
  await page.setViewportSize({ width: 1440, height: 2000 });
  const prefix = `${base}/api/projects/${expected.project_id}/video-localization`;
  const read = async () => (await (await page.request.get(prefix)).json());
  const resultId = expected.alternate_result_id;
  const dragHistory = async () => {
    const subtitle = page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]');
    const subtitleBox = await subtitle.boundingBox();
    await subtitle.click({ position: { x: subtitleBox.width - 8, y: subtitleBox.height / 2 } });
    await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
    await page.getByRole('tab', { name: /^全部片段/ }).click();
    const wave = page.locator(`.tts-history .history-row[data-result-id="${resultId}"] .wave-track`);
    await wave.scrollIntoViewIfNeeded();
    const from = await wave.boundingBox();
    const lane = await page.locator('[data-track-id="dub"][data-dub-lane="0"]').boundingBox();
    const placed = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname.endsWith(`/timeline-clips/history/${resultId}/apply`));
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(lane.x + lane.width * .55, lane.y + lane.height / 2, { steps: 12 });
    await page.getByLabel('配音落位预览', { exact: true }).waitFor();
    await page.mouse.up();
    const response = await placed;
    assert.ok(response.ok(), await response.text());
    const request = response.request().postDataJSON();
    assert.ok(request.request_id, 'One real drag must have a stable placement command ID');
    return { request, url: response.url() };
  };
  const before = await read();
  const first = await dragHistory();
  const clipId = first.request.new_clip_id;
  const imported = await read();
  assert.equal(imported.timeline_clips.length, before.timeline_clips.length + 1);
  const clip = page.locator(`[data-audio-clip-id="${clipId}"]`);
  await verifyTrackIdentity('history placement receipt');
  await clip.scrollIntoViewIfNeeded();
  await clip.click();
  await page.waitForFunction(id => document.querySelector(`[data-audio-clip-id="${id}"]`)?.classList.contains('selected'), clipId);
  const box = await clip.boundingBox();
  const handle = await clip.locator('.clip-handle.start').boundingBox();
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + Math.max(20, box.width * .25), handle.y + handle.height / 2, { steps: 5 });
  await page.mouse.up();
  const importedClip = imported.timeline_clips.find(item => item.clip_id === clipId);
  let edited;
  const saveDeadline = Date.now() + 20000;
  while (Date.now() < saveDeadline) {
    edited = await read();
    if (edited.timeline_clips.find(item => item.clip_id === clipId)?.source_start_ms > importedClip.source_start_ms) break;
    await page.waitForTimeout(100);
  }
  const editedClip = edited.timeline_clips.find(item => item.clip_id === clipId);
  assert.ok(editedClip.source_start_ms > importedClip.source_start_ms, 'History trim must persist through the public save path');
  const replay = await page.request.post(first.url, { data: first.request });
  assert.ok(replay.ok(), await replay.text());
  assert.deepEqual((await read()).timeline_clips, edited.timeline_clips, 'Late placement retry must not reset any edited clip');
  await page.reload({ waitUntil: 'networkidle' });
  await clip.waitFor();
  assert.deepEqual((await read()).timeline_clips, edited.timeline_clips);
  const second = await dragHistory();
  assert.notEqual(second.request.request_id, first.request.request_id, 'A separate intentional drag needs a new command ID');
  const appended = await read();
  assert.equal(appended.timeline_clips.length, edited.timeline_clips.length + 1);
  for (const item of edited.timeline_clips) assert.deepEqual(appended.timeline_clips.find(current => current.clip_id === item.clip_id), item);
  await verifyTrackIdentity('second history placement');
  const secondClip = page.locator(`[data-audio-clip-id="${second.request.new_clip_id}"]`);
  await secondClip.click();
  const deleted = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/timeline-edit'));
  await page.getByRole('button', { name: '删除', exact: true }).click();
  assert.ok((await deleted).ok());
  await secondClip.waitFor({ state: 'detached' });
  const remaining = await read();
  assert.deepEqual(remaining.timeline_clips, edited.timeline_clips, 'Deleting one history copy must preserve the other takes');
  await verifyTrackIdentity('history copy deletion');
  await page.reload({ waitUntil: 'networkidle' });
  assert.deepEqual((await read()).timeline_clips, remaining.timeline_clips);
  assert.equal(await secondClip.count(), 0, 'Deleted history copy must stay absent after refresh');
  await verifyTrackIdentity('history copy deletion after refresh');
  await clip.click();
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^全部片段/ }).click();
  const player = page.locator(`.tts-history .history-row[data-result-id="${resultId}"] .audio-waveform`);
  // The short fixture's audio element is unmounted on ended. Capture actual
  // clock progress before clicking so a busy runner cannot miss its lifetime.
  await player.evaluate(element => {
    const recordProgress = event => {
      if (event.target instanceof HTMLAudioElement && event.target.currentTime > .05) {
        element.dataset.observedPlaybackProgress = 'true';
      }
    };
    element.addEventListener('timeupdate', recordProgress, true);
    element.addEventListener('ended', recordProgress, true);
  });
  await player.getByRole('button', { name: '播放配音', exact: true }).click();
  await page.waitForFunction(id => document.querySelector(`.tts-history .history-row[data-result-id="${id}"] .audio-waveform`)?.dataset.observedPlaybackProgress === 'true', resultId).catch(async error => {
    console.error(JSON.stringify({ historyPlaybackDiagnostics: await player.evaluate(element => {
      const audio = element.querySelector('audio');
      return { connected: element.isConnected, observed: element.dataset.observedPlaybackProgress,
        audio: audio ? { paused: audio.paused, time: audio.currentTime, duration: audio.duration,
          readyState: audio.readyState, networkState: audio.networkState, error: audio.error?.code,
          buffered: Array.from({ length: audio.buffered.length }, (_, i) => [audio.buffered.start(i), audio.buffered.end(i)]) } : null };
    }) }));
    throw error;
  });
  const pauseHistory = player.getByRole('button', { name: '暂停配音', exact: true });
  if (await pauseHistory.count()) await pauseHistory.click();
  console.log(JSON.stringify({ historyPlacementReplay: 'passed', checks: ['real history drag', 'saved prefix trim', 'same request replay preserves edits', 'new drag appends', 'delete copy without remnants', 'track header and canvas alignment', 'refresh persistence', 'audio playback'] }));
}

try {
  const url = `${base}/video-localization?project_id=${expected.project_id}`;
  await page.goto(url, { waitUntil: 'networkidle' });
  const subtitle = page.locator('[data-track-id="localizedSubtitles"] [data-subtitle-item-id="subtitle"]');
  const subtitleBounds = await subtitle.boundingBox();
  // The partial-overlap fixture covers the start of this subtitle. Select its
  // exposed tail exactly as a user can, without bypassing pointer hit testing.
  await subtitle.click({ position: { x: subtitleBounds.width - 8, y: subtitleBounds.height / 2 } });
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^当前片段/ }).click();
  const reuse = page.locator('.tts-history .reuse-button').first();
  await reuse.scrollIntoViewIfNeeded();
  const generation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/generate' && response.request().method() === 'POST');
  await reuse.click();
  const started = await generation;
  assert.ok(started.ok(), 'The actual reuse button must submit a generation');
  const task = await started.json();
  let updated;
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    updated = await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json();
    const workflow = updated.tts_tasks.find(item => item.generation_task_id === task.task_id);
    if (workflow && ['success', 'failed'].includes(workflow.stages[1].status)) {
      assert.equal(workflow.stages[1].status, 'success', JSON.stringify(workflow));
      break;
    }
    await page.waitForTimeout(100);
  }
  assert.equal(updated.timeline_clips.length, expected.clips.length + 1, 'Web reuse must append exactly one clip');
  for (const clip of expected.clips) {
    assert.deepEqual(updated.timeline_clips.find(item => item.clip_id === clip.clip_id), clip, 'Web reuse must not alter any old clip');
  }
  const newClip = updated.timeline_clips.find(item => !expected.clips.some(old => old.clip_id === item.clip_id));
  assert.ok(updated.tts_tasks.some(item => item.generation_task_id === task.task_id && item.stages[1].status === 'success'));
  assert.ok(updated.timeline_clips.filter(item => item.clip_id !== newClip.clip_id)
    .every(item => item.dub_lane !== newClip.dub_lane || item.end_ms <= newClip.start_ms || item.start_ms >= newClip.end_ms), 'Web reuse must use a free lane');
  expected.clips = updated.timeline_clips;
  expected.workflow_count = updated.tts_tasks.length;
  for (const phase of ['initial', 'refreshed']) {
    if (phase === 'refreshed') await page.reload({ waitUntil: 'networkidle' });
    for (const clip of expected.clips) {
      const rendered = page.locator(`[data-audio-clip-id="${clip.clip_id}"]`);
      await rendered.waitFor({ state: 'attached' });
      assert.equal(await rendered.count(), 1, `Duplicate or missing clip ${clip.clip_id}`);
    }
    const response = await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`);
    assert.equal(response.status(), 200);
    const draft = await response.json();
    assert.deepEqual(draft.timeline_clips, expected.clips, 'Page open/refresh must preserve all clip metadata');
    assert.deepEqual(draft.timeline_clips.find(clip => clip.clip_id === 'old'), expected.old_clip);
    assert.equal(draft.tts_tasks.filter(task => task.stages[1].status === 'success').length, expected.workflow_count);
    await page.screenshot({ path: resolve(process.env.DUBBING_APPEND_ARTIFACT_DIR, `append-${phase}.png`), fullPage: true });
  }
  // Successful workflow deletion is exposed through the public API. Its
  // stable slot has already adopted a different history result above.
  const deletion = await page.request.delete(`${base}/api/projects/${expected.project_id}/video-localization/tts/tasks/${expected.obsolete_workflow_id}`);
  assert.ok(deletion.ok());
  const afterDelete = await deletion.json();
  assert.deepEqual(afterDelete.timeline_clips, expected.clips, 'Deleting an obsolete workflow must preserve all replacement media');
  assert.ok(!afterDelete.tts_tasks.some(item => item.workflow_id === expected.obsolete_workflow_id));
  await page.reload({ waitUntil: 'networkidle' });
  for (const clip of expected.clips) {
    await page.locator(`[data-audio-clip-id="${clip.clip_id}"]`).waitFor({ state: 'attached' });
  }
  assert.deepEqual((await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json()).timeline_clips, expected.clips);
  await page.screenshot({ path: resolve(process.env.DUBBING_APPEND_ARTIFACT_DIR, 'obsolete-workflow-deleted.png'), fullPage: true });
  // Actual saved razor split -> history-adoption button -> undo hotkey.
  const parent = page.locator(`[data-audio-clip-id="${expected.first_clip_id}"]`);
  const childId = `${expected.first_clip_id}_part_2`;
  const child = page.locator(`[data-audio-clip-id="${childId}"]`);
  await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
  const splitSaved = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/timeline-edit'), { timeout: 20000 });
  const parentBox = await parent.boundingBox();
  await parent.click({ position: { x: parentBox.width / 2, y: parentBox.height / 2 } });
  await child.waitFor();
  assert.ok((await splitSaved).ok());
  const undo = page.getByRole('button', { name: '撤销时间线编辑', exact: true });
  assert.equal(await undo.isEnabled(), true, 'Saved same-generation split remains undoable');
  await page.getByRole('button', { name: '剃刀工具', exact: true }).click();
  const lane = child.locator('..');
  const laneBox = await lane.boundingBox();
  await lane.click({ position: { x: laneBox.width - 8, y: laneBox.height / 2 } });
  await child.click();
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^当前片段/ }).click();
  const apply = page.locator(`.history-row[data-result-id="${expected.alternate_result_id}"] .apply-button`);
  const adopted = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname.includes(`/history/${expected.alternate_result_id}`));
  await apply.click();
  const adoptionResponse = await adopted;
  assert.ok(adoptionResponse.ok());
  assert.equal(adoptionResponse.request().postDataJSON().clip_id, childId);
  assert.deepEqual((await adoptionResponse.json()).affected_clip_ids, [childId]);
  await page.waitForFunction(() => document.querySelector('[aria-label="撤销时间线编辑"]')?.disabled === true);
  const beforeUndo = (await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json()).timeline_clips;
  assert.equal(beforeUndo.find(clip => clip.clip_id === childId)?.result_id, expected.alternate_result_id);
  await page.locator('.cut-timeline').click({ position: { x: 5, y: 5 } });
  await page.keyboard.press('Meta+z');
  await page.reload({ waitUntil: 'networkidle' });
  await child.waitFor();
  const afterUndo = (await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json()).timeline_clips;
  assert.deepEqual(afterUndo, beforeUndo, 'Old split undo must not delete a newly adopted child');
  await page.screenshot({ path: resolve(process.env.DUBBING_APPEND_ARTIFACT_DIR, 'replacement-child-retained.png'), fullPage: true });
  // Rebuild the real production plan, then deliver its obsolete frozen result.
  // Only the callback fixture is seeded; no rejection/projection is mocked.
  const prefix = `${base}/api/projects/${expected.project_id}/video-localization`;
  const snapshot = await (await page.request.get(`${prefix}/dubbing/snapshot`)).json();
  const planInput = { schema_version: 'dubbing-generation-plan-input-v1',
    source_revision: snapshot.source_revision,
    semantic_units: snapshot.semantic_units.map(unit => ({ ...unit, speech_policy: 'translate', scene_id: 'fixture-scene' })),
    boundaries: snapshot.boundaries, policy: { preferred_group_units: 1, hard_max_group_units: 2 } };
  const oldPlanResponse = await page.request.post(`${prefix}/dubbing/plan`, { data: planInput });
  assert.ok(oldPlanResponse.ok());
  const oldPlan = await oldPlanResponse.json();
  const newPlanResponse = await page.request.post(`${prefix}/dubbing/plan`, { data: planInput });
  assert.ok(newPlanResponse.ok());
  assert.ok((await newPlanResponse.json()).plan_revision > oldPlan.plan_revision);
  const group = oldPlan.groups.find(group => group.subtitle_ids.includes('subtitle'));
  for (const [entrypoint, groupId] of [['immediate', group.group_id], ['replay', `${group.group_id}-late`]]) {
    const retired = await page.request.post(`${base}/api/__content_acceptance/obsolete-placement`, { data: {
      result_id: newClip.result_id, entrypoint, plan_revision: oldPlan.plan_revision,
      group_id: groupId, subtitle_ids: group.subtitle_ids } });
    assert.ok(retired.ok(), await retired.text());
    assert.deepEqual(await retired.json(), { status: 'abandoned', attempts: 1,
      history_preserved: true, audio_preserved: true, timeline_preserved: true });
  }
  await page.reload({ waitUntil: 'networkidle' });
  await child.waitFor();
  assert.deepEqual((await (await page.request.get(prefix)).json()).timeline_clips, beforeUndo);
  await child.click();
  await page.locator('.inspector-mode-tabs').getByRole('button', { name: '配音', exact: true }).click();
  await page.getByRole('tab', { name: /^当前片段/ }).click();
  await page.locator(`.history-row[data-result-id="${expected.alternate_result_id}"]`).waitFor();
  await page.screenshot({ path: resolve(process.env.DUBBING_APPEND_ARTIFACT_DIR, 'obsolete-result-history-retained.png'), fullPage: true });
  await verifyTimelineControls(page, base, expected.project_id, process.env.DUBBING_APPEND_ARTIFACT_DIR);
  await verifyHistoryPlacementReplay();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ append_browser: 'passed', clips: expected.clips.length,
    checks: ['same target append', 'reuse append', 'free lanes', 'partial group overlap preserved', 'explicit ID replacement', 'obsolete workflow deletion', 'saved split history adoption undo', 'obsolete plan delivery retired with audio preserved', 'refresh persistence'],
    trigger: 'actual history reuse button plus public handoff/generate/history APIs with fixed providers' }));
} catch (error) {
  await page.screenshot({ path: resolve(process.env.DUBBING_APPEND_ARTIFACT_DIR, 'append-failed.png'), fullPage: true });
  console.error((await page.locator('body').innerText()).slice(-5000));
  throw error;
} finally {
  await page.context().request.dispose();
  await page.context().close();
  await browser.close();
}
