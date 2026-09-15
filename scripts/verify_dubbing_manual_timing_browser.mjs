import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.RECOVERY_URL;
const expected = JSON.parse(process.env.RECOVERY_EXPECTED);
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base));
assert.ok(![5173, 18000].includes(Number(new URL(base).port)));
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}));
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const prefix = `${base}/api/projects/${expected.project_id}/video-localization`;
  const get = async path => {
    const response = await page.request.get(`${prefix}${path}`);
    assert.ok(response.ok(), await response.text());
    return response.json();
  };
  const post = async (path, data, status = 200) => {
    const response = await page.request.post(`${prefix}${path}`, { data });
    assert.equal(response.status(), status, await response.text());
    return response.json();
  };
  await page.goto(`${base}/video-localization?project_id=${expected.project_id}`);
  assert.equal(expected.generated_before, 1, 'Proven overflow must hand off before automatic TTS retry');
  const capacityHandoff = await post('/dubbing/production-run/execute', {
    scope: 'single_group', group_id: expected.group_id,
  });
  assert.equal(capacityHandoff.status, 'needs_attention');
  assert.equal(capacityHandoff.required_action, 'resolve_capacity');
  const draft = await get('');
  const input = [...draft.dubbing_production.candidate_inputs].reverse().find(item => item.group_id === expected.group_id && item.task_status === 'success');
  assert.ok(input, 'The fixture must have a successful persistent audio candidate');
  const candidate = draft.generated_candidates.find(item => item.candidate_id === input.candidate_id || item.result_id === input.candidate_id);
  const workflow = draft.tts_tasks.find(item => item.result_id === input.candidate_id || `candidate_${item.generation_task_id}` === input.candidate_id);
  const resultId = candidate?.result_id ?? workflow?.result_id;
  assert.ok(resultId);
  const plan = draft.dubbing_production.active_plan;
  const group = plan.groups.find(item => item.group_id === expected.group_id);
  const sourceCueIds = [...new Set(plan.semantic_units.filter(unit => group.unit_ids.includes(unit.unit_id)).flatMap(unit => unit.source_cue_ids))];
  const contentRequest = {
    schema_version: 'dubbing-candidate-content-evidence-request-v1',
    expected_repository_revision: Number((await get('/workspace-revision')).revision),
    source_revision: input.source_revision, plan_revision: input.plan_revision,
    group_id: expected.group_id, candidate_id: input.candidate_id, result_id: resultId,
  };
  const content = await post(`/dubbing/candidates/${input.candidate_id}/content-evidence`, contentRequest);
  assert.equal(content.evidence.status, 'complete');
  assert.equal(content.evidence.audio_sha256, input.audio_sha256);
  assert.equal(content.evidence.transcript, group.spoken_text);
  const contentCalls = (await (await page.request.get(`${base}/api/__content_acceptance/state`)).json()).asr_calls;
  await post(`/dubbing/candidates/${input.candidate_id}/content-evidence`, {
    ...contentRequest, expected_repository_revision: Number((await get('/workspace-revision')).revision),
  });
  assert.equal((await (await page.request.get(`${base}/api/__content_acceptance/state`)).json()).asr_calls, contentCalls);
  // A real public history append simulates a user-retained primary take.
  await post(`/timeline-clips/history/${resultId}/apply`, {
    request_id: 'manual-timing-user-copy', segment_id: group.subtitle_ids[0],
    new_clip_id: 'manual-timing-primary', start_ms: 0, dub_lane: 0, force_new: true,
  });
  const before = await get('');
  const primaryBefore = before.timeline_clips.filter(clip => clip.track_id === 'dub' && clip.dub_lane === 0);
  assert.ok(primaryBefore.length);
  const revision = await get('/workspace-revision');
  const evidence = (strategy, outcome, reason, attempt_count = 0) => ({
    strategy, outcome, reason, attempt_count,
    evidence_ids: [`fixed-provider:${input.candidate_id}:${strategy}`],
  });
  const command = {
    schema_version: 'dubbing-manual-timing-deferral-request-v1',
    request_id: 'manual-timing-park', expected_repository_revision: Number(revision.revision),
    source_revision: input.source_revision, plan_revision: input.plan_revision,
    group_id: expected.group_id, candidate_id: input.candidate_id, result_id: resultId,
    parked_clip_id: 'manual-timing-secondary', available_duration_ms: (plan.groups[plan.groups.indexOf(group) + 1]?.target_start_ms ?? group.target_end_ms) - group.target_start_ms,
    candidate_duration_ms: input.audio.duration_ms, audio_sha256: input.audio_sha256,
    content_verification_status: 'verified_complete', content_verification_evidence_ids: [content.evidence_id],
    semantic_boundary_review: 'agent_asserted_complete', semantic_boundary_evidence_ids: ['fixed-provider:continuous-expression'],
    naturalness_review: 'not_claimed',
    recovery_evidence: [
      evidence('verify_window_and_group', 'applied', 'Fixed fixture has a 3000ms slot and complete 4000ms audio.'),
      evidence('safe_gap_edit', 'not_applicable', 'Fixed continuous voiced signal has no safe removable gap.'),
      evidence('allowed_speed', 'not_applicable', 'Fixture preserves the frozen model speed.'),
      evidence('whole_regeneration', expected.generated_before > 1 ? 'no_improvement' : 'not_applicable',
        expected.generated_before > 1 ? 'The initial take and bounded retry both have the same 4000ms duration.' : 'The fixed provider emits the frozen full utterance at 4000ms; unchanged input cannot fit this fixture window.',
        Math.max(0, expected.generated_before - 1)),
      evidence('semantic_split', 'not_applicable', 'This fixture preserves one continuous utterance.'),
      evidence('equivalent_text_compression', 'not_applicable', 'The fixture freezes the supplied wording to test timing-only deferral.'),
    ],
  };
  const missingStep = await post('/dubbing/production-run/manual-timing-deferral', {
    ...command, recovery_evidence: command.recovery_evidence.slice(1),
  }, 400);
  assert.equal(missingStep.error.code, 'INVALID_REQUEST');
  assert.deepEqual((await get('')).timeline_clips, before.timeline_clips);
  const receipt = await post('/dubbing/production-run/manual-timing-deferral', command);
  const saved = await get('');
  const parked = saved.timeline_clips.find(clip => clip.clip_id === command.parked_clip_id);
  assert.ok(parked);
  assert.equal(parked.dub_lane, 1);
  assert.equal(parked.source_end_ms - parked.source_start_ms, command.candidate_duration_ms);
  assert.deepEqual(parked.target_subtitle_ids, group.subtitle_ids);
  assert.deepEqual(parked.source_cue_ids, sourceCueIds);
  assert.deepEqual(saved.timeline_clips.filter(clip => clip.track_id === 'dub' && clip.dub_lane === 0), primaryBefore);
  await post('/dubbing/production-run/manual-timing-deferral', command);
  assert.equal((await get('')).timeline_clips.filter(clip => clip.clip_id === parked.clip_id).length, 1);
  await post('/dubbing/production-run/execute', { scope: 'single_group', group_id: expected.group_id });
  const run = await get('/dubbing/production-run');
  assert.equal(run.groups.find(item => item.group_id === expected.group_id).stage, 'deferred_manual_timing');
  const resumedRange = await post('/dubbing/production-run/execute', {
    scope: 'all_remaining', start_group_id: expected.group_id, end_group_id: expected.group_id,
  });
  assert.equal(resumedRange.status, 'complete', JSON.stringify(resumedRange));
  assert.equal(resumedRange.completion.automated_production_status, 'resolved');
  assert.ok(resumedRange.completion.deferred_manual_timing_clip_ids.includes(parked.clip_id));
  assert.equal((await (await page.request.get(`${base}/api/__content_acceptance/state`)).json()).generated, expected.generated_before);
  await page.reload();
  await page.locator(`[data-audio-clip-id="${parked.clip_id}"]`).first().waitFor();
  const refreshed = await get('');
  assert.deepEqual(refreshed.timeline_clips, saved.timeline_clips);
  const completion = await get('/dubbing/completion');
  assert.ok(completion.deferred_manual_timing_clip_ids.includes(parked.clip_id));
  assert.notEqual(completion.status, 'complete');
  // A parked group is resolved for automation, not a barrier that strands
  // the next group or authorizes another take for this one.
  const nextGroup = plan.groups[plan.groups.indexOf(group) + 1];
  assert.ok(nextGroup, 'The fixture must exercise continuation beyond deferral');
  const nextSubtitle = refreshed.localized_subtitles.find(item => item.subtitle_id === nextGroup.subtitle_ids[0]);
  const edited = await page.request.patch(`${prefix}/localized-subtitles/${nextSubtitle.subtitle_id}/edit`, {
    data: { text: `${nextSubtitle.text}（显示修订）`, tts_text: nextSubtitle.tts_text || nextSubtitle.text },
  });
  assert.ok(edited.ok(), await edited.text());
  const rebound = await get('');
  const reboundReceipt = rebound.dubbing_production.manual_timing_deferrals.find(item => item.parked_clip_id === parked.clip_id);
  assert.ok(reboundReceipt);
  assert.equal(reboundReceipt.plan_revision, rebound.dubbing_production.active_plan.plan_revision);
  assert.equal(reboundReceipt.evidence_origin_plan_revision, receipt.disposition.evidence_origin_plan_revision);
  assert.deepEqual(rebound.timeline_clips, saved.timeline_clips);
  await page.reload();
  await page.locator(`[data-audio-clip-id="${parked.clip_id}"]`).first().waitFor();
  assert.equal((await get('/dubbing/production-run')).groups.find(item => item.group_id === group.group_id).stage, 'deferred_manual_timing');
  const continued = await post('/dubbing/production-run/execute', {
    scope: 'all_remaining', start_group_id: group.group_id, end_group_id: nextGroup.group_id,
    max_in_flight_groups: 1,
  });
  assert.equal(continued.status, 'queued', JSON.stringify(continued));
  assert.equal(continued.group_id, nextGroup.group_id);
  const afterContinue = await get('');
  assert.deepEqual(afterContinue.timeline_clips, saved.timeline_clips);
  assert.equal(afterContinue.tts_tasks.filter(item => item.segment_id === group.subtitle_ids[0]).length,
    saved.tts_tasks.filter(item => item.segment_id === group.subtitle_ids[0]).length);

  // Once the original deferral has safely continued, a real user trim invalidates
  // that timing evidence. Resuming the affected group must preserve the edit and
  // surface manual timeline work instead of generating or restoring the old take.
  const lane2BeforeEdit = (await get('')).timeline_clips.find(clip => clip.clip_id === parked.clip_id);
  assert.ok(lane2BeforeEdit);
  const groupTasksBeforeEdit = (await get('')).tts_tasks
    .filter(item => item.segment_id === group.subtitle_ids[0])
    .map(item => [item.generation_task_id, item.result_id]);
  const parkedElement = page.locator(`[data-audio-clip-id="${parked.clip_id}"]`).first();
  await parkedElement.scrollIntoViewIfNeeded();
  await parkedElement.click();
  const parkedBounds = await parkedElement.boundingBox();
  const parkedEndHandle = await parkedElement.locator('.clip-handle.end').boundingBox();
  assert.ok(parkedBounds && parkedEndHandle, 'Lane 2 must expose a real timeline trim handle');
  const lane2EditSaved = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/timeline-edit'));
  await page.mouse.move(parkedEndHandle.x + parkedEndHandle.width / 2, parkedEndHandle.y + parkedEndHandle.height / 2);
  await page.mouse.down();
  await page.mouse.move(parkedEndHandle.x + parkedEndHandle.width / 2 - parkedBounds.width * 0.2,
    parkedEndHandle.y + parkedEndHandle.height / 2, { steps: 4 });
  await page.mouse.up();
  const lane2EditResponse = await lane2EditSaved;
  assert.ok(lane2EditResponse.ok(), await lane2EditResponse.text());
  await page.waitForFunction(() => !document.body.innerText.includes('正在保存'));
  const lane2EditedDraft = await get('');
  const lane2EditedClip = lane2EditedDraft.timeline_clips.find(clip => clip.clip_id === parked.clip_id);
  assert.ok(lane2EditedClip);
  assert.equal(lane2EditedClip.dub_lane, 1);
  assert.ok(lane2EditedClip.end_ms < lane2BeforeEdit.end_ms && lane2EditedClip.source_end_ms < lane2BeforeEdit.source_end_ms,
    JSON.stringify({before:lane2BeforeEdit,after:lane2EditedClip}));
  assert.equal(lane2EditedClip.start_ms, lane2BeforeEdit.start_ms);
  assert.equal(lane2EditedClip.source_start_ms, lane2BeforeEdit.source_start_ms);
  assert.equal(lane2EditedClip.result_id, lane2BeforeEdit.result_id);
  await post('/dubbing/production-run/execute', { scope: 'single_group', group_id: group.group_id });
  const editedRun = await get('/dubbing/production-run');
  assert.equal(editedRun.groups.find(item => item.group_id === group.group_id).stage, 'needs_timeline_edit');
  const afterEditedResume = await get('');
  assert.deepEqual(afterEditedResume.timeline_clips.find(clip => clip.clip_id === parked.clip_id), lane2EditedClip);
  assert.deepEqual(afterEditedResume.tts_tasks.filter(item => item.segment_id === group.subtitle_ids[0])
    .map(item => [item.generation_task_id, item.result_id]), groupTasksBeforeEdit);
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator(`[data-audio-clip-id="${parked.clip_id}"]`).first().waitFor();
  assert.deepEqual((await get('')).timeline_clips.find(clip => clip.clip_id === parked.clip_id), lane2EditedClip);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ manualTimingDeferral: 'passed', trigger: 'public API, real lane 2 timeline trim, automatic continuation and Web refresh', generated: expected.generated_before, rangeResume: resumedRange.status, receiptVersion: receipt.schema_version }));
} finally {
  await browser.close();
}
