#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.BRIEF_JOURNAL_E2E_URL;
const artifacts = process.env.BRIEF_JOURNAL_E2E_ARTIFACT_DIR;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000, 51992].includes(Number(new URL(base).port)) || !artifacts) {
  throw new Error('An isolated acceptance URL and artifact directory are required');
}
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
async function api(path, data) {
  const response = data === undefined ? await page.request.get(base + '/api' + path) : await page.request.post(base + '/api' + path, { data });
  const payload = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(payload));
  return payload;
}
async function poll(action, condition) {
  for (let attempt = 0; attempt < 200; attempt++) {
    const value = await action();
    if (condition(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Acceptance state did not settle');
}
async function runBatchRecovery(state) {
  const prefix = `/projects/${state.project_id}/video-localization`;
  const generation = state.scenario === 'generation';
  const scenario = state.scenario;
  const target = generation ? 'generate_localization_spoken_script' : 'adjudicate_localization_evidence_v3';
  const counter = generation ? 'generation_calls' : 'image_calls';
  const order = generation ? 'generation_order' : 'image_order';
  const inputs = generation ? 'generation_inputs' : 'image_hashes';
  const firstId = generation ? 'chunk_0001' : 'question_0001';
  const secondId = generation ? 'chunk_0002' : 'question_0002';
  const firstBatchId = generation ? 'generation-chunk_0001' : 'evidence-images-01';
  const secondBatchId = generation ? 'generation-chunk_0002' : 'evidence-images-02';
  const refusalText = generation ? '第二生成批模拟额度拒绝' : '第二图片批模拟额度拒绝';
  const paragraphs = generation ? ['你好，世界。', '再见，世界。']
    : ['固定画面证据已核对：question_0001', '固定画面证据已核对：question_0002'];
  const workflow = await api(prefix + '/workflows/localization');
  const label = workflow.stages.flatMap(stage => stage.atomic_tasks).find(task => task.id === target).label;
  const request = { execution_mode: 'development_target', development_target_step_id: target,
    development_session_id: state.session_id, force_development_target: true, profile_id: state.profile_id };
  const waitOperation = operation => poll(() => api(prefix + `/operations/${operation.operation_id}`),
    op => ['success', 'failed'].includes(op.status));
  const upstream = await api(prefix + '/operations/localization', { ...request,
    development_target_step_id: generation ? 'lock_localization_creation_context' : 'analyze_localization_document' });
  const upstreamDone = await waitOperation(upstream);
  assert.equal(upstreamDone.status, 'success', JSON.stringify({ error: upstreamDone.error_message, stage: upstreamDone.result_summary.stage_id }));
  assert.equal((await api('/__brief_acceptance/state')).text_calls, generation ? 3 : 2);
  await page.goto(`${base}/video-localization?project_id=${state.project_id}`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '任务', exact: true }).click();
  const first = await api(prefix + '/operations/localization', request);
  await poll(() => api('/__brief_acceptance/state'), value => value[counter] === 1);
  await page.locator('.task-row.running').filter({ hasText: '本土化' }).waitFor();
  await page.screenshot({ path: resolve(artifacts, `${scenario}-running.png`), fullPage: true });
  await api('/__brief_acceptance/release', { phase: 'initial' });
  const failed = await waitOperation(first);
  assert.equal(failed.status, 'failed');
  assert.ok(JSON.stringify(failed).includes(refusalText));
  const initial = await api('/__brief_acceptance/state');
  assert.equal(initial[counter], 2);
  assert.equal(initial.project_content_unchanged, true, JSON.stringify(initial.draft_changed_fields));
  assert.equal(initial.journal.length, 2);
  const firstBatch = initial.journal.find(record => record.batch_id === firstBatchId);
  assert.equal(firstBatch.status, generation ? 'validation_passed' : 'response_received');
  const refusal = initial.journal.find(record => record.batch_id === secondBatchId);
  assert.equal(refusal.status, 'call_failed');
  assert.equal(refusal.call_error_code, 'codex_cli_rate_limited');
  async function inspectHistory(count, success) {
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '任务', exact: true }).click();
    await page.waitForFunction(n => document.querySelectorAll('.history-row').length === n, count);
    const rejected = page.locator('.history-row.failed');
    assert.equal(await rejected.count(), 1);
    await rejected.locator('.history-summary').click();
    assert.ok((await rejected.innerText()).includes(refusalText));
    if (!success) {
      await page.screenshot({ path: resolve(artifacts, `${scenario}-failed.png`), fullPage: true, animations: 'disabled' });
      return;
    }
    const row = page.locator('.history-row:not(.failed)').first();
    await row.locator('.history-summary').click();
    await row.getByRole('button', { name: `查看“${label}”的结果`, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    for (const paragraph of paragraphs) {
      assert.ok((await dialog.innerText()).includes(paragraph));
    }
    await dialog.getByText(paragraphs[1], { exact: true }).first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(artifacts, `${scenario}-history-${count}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭步骤结果' }).click();
  }
  await inspectHistory(2, false);
  const second = await api(prefix + '/operations/localization', request);
  await poll(() => api('/__brief_acceptance/state'), value => value[counter] === 3);
  await page.locator('.task-row.running').filter({ hasText: '本土化' }).waitFor();
  await page.screenshot({ path: resolve(artifacts, `${scenario}-recovery-running.png`), fullPage: true });
  await api('/__brief_acceptance/release', { phase: 'recovery' });
  const recovered = await waitOperation(second);
  assert.equal(recovered.status, 'success', JSON.stringify(recovered.result_summary));
  await inspectHistory(3, true);
  const third = await api(prefix + '/operations/localization', request);
  assert.equal((await waitOperation(third)).status, 'success');
  const after = await api('/__brief_acceptance/state');
  assert.equal(after[counter], 3);
  assert.equal(after.text_calls, generation ? 3 : 2);
  assert.equal(after.project_content_unchanged, true, JSON.stringify(after.draft_changed_fields));
  assert.deepEqual(after[order], [firstId, secondId, secondId]);
  assert.deepEqual(after[inputs][1], after[inputs][2]);
  assert.equal(after.journal.length, 3);
  assert.equal(after.journal.find(record => record.batch_id === firstBatchId).candidate_fingerprint, firstBatch.candidate_fingerprint);
  assert.equal(after.journal.find(record => record.call_error_code === 'codex_cli_rate_limited').file_sha256, refusal.file_sha256);
  assert.equal(after.journal.filter(record => record.status === 'validation_passed').length, 2);
  assert.deepEqual(await api(prefix + `/operations/${first.operation_id}`), failed);
  await inspectHistory(4, true);
  await inspectHistory(4, true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ [`${scenario}JournalWeb`]: 'passed', operationIds: [first.operation_id, second.operation_id, third.operation_id],
    upstreamTextCalls: after.text_calls, batchCalls: after[counter], batchOrder: after[order],
    ...(generation ? {} : { imagesPerBatch: after.image_hashes.map(hashes => hashes.length) }), originalRejectionUnchanged: true,
    firstBatchCandidateUnchanged: true, projectContentUnchanged: after.project_content_unchanged, consoleErrors: errors }));
}
try {
  const state = await api('/__brief_acceptance/state');
  if (['evidence', 'generation'].includes(state.scenario)) {
    await runBatchRecovery(state);
  } else {
  const prefix = `/projects/${state.project_id}/video-localization`;
  const workflow = await api(prefix + '/workflows/localization');
  const targetLabel = workflow.stages.flatMap(stage => stage.atomic_tasks).find(task => task.id === 'analyze_localization_document').label;
  await page.goto(`${base}/video-localization?project_id=${state.project_id}`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: '任务', exact: true }).click();
  const request = { execution_mode: 'development_target', development_target_step_id: 'analyze_localization_document',
    development_session_id: state.session_id, force_development_target: true, profile_id: state.profile_id };
  const first = await api(prefix + '/operations/localization', request);
  await poll(() => api('/__brief_acceptance/state'), value => value.model_calls === 1);
  const running = page.locator('.task-row.running').filter({ hasText: '本土化' });
  await running.waitFor();
  assert.match(await running.innerText(), /全文|篇章/);
  await page.screenshot({ path: resolve(artifacts, 'brief-running.png'), fullPage: true });
  await api('/__brief_acceptance/release', { phase: 'initial' });
  const firstDone = await poll(() => api(prefix + `/operations/${first.operation_id}`), op => ['success', 'failed'].includes(op.status));
  assert.equal(firstDone.status, 'failed', JSON.stringify(firstDone.result_summary));
  assert.match(JSON.stringify(firstDone), /模拟额度拒绝/);
  const afterFailure = await api('/__brief_acceptance/state');
  assert.equal(afterFailure.model_calls, 2);
  assert.equal(afterFailure.outline_calls, 1);
  assert.equal(afterFailure.detail_calls, 1);
  const rejected = afterFailure.journal.find(record => record.call_error_code === 'codex_cli_rate_limited');
  assert.ok(rejected);
  assert.equal(rejected.status, 'call_failed');
  assert.equal(rejected.recovery_ordinal, 0);
  assert.equal(rejected.recovery_execution_id, first.operation_id);
  const outline = afterFailure.journal.find(record => record.batch_id === 'brief-outline');
  assert.equal(outline.status, 'validation_passed');
  async function inspectHistory(expectedCount, inspectSuccess, inspectReceipts = false) {
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '任务', exact: true }).click();
    const rows = page.locator('.history-row').filter({ hasText: '本土化' });
    await page.waitForFunction(count => document.querySelectorAll('.history-row').length >= count, expectedCount);
    assert.equal(await rows.count(), expectedCount);
    const failed = page.locator('.history-row.failed').filter({ hasText: '本土化' });
    assert.equal(await failed.count(), 1);
    if (await failed.locator('.history-summary').getAttribute('aria-expanded') !== 'true') await failed.locator('.history-summary').click();
    assert.match(await failed.innerText(), /额度|拒绝/);
    if (!inspectSuccess) {
      await page.screenshot({ path: resolve(artifacts, 'brief-failed-history.png'), fullPage: true, animations: 'disabled' });
      return;
    }
    const row = page.locator('.history-row:not(.failed)').filter({ hasText: '本土化' }).first();
    if (await row.locator('.history-summary').getAttribute('aria-expanded') !== 'true') await row.locator('.history-summary').click();
    await row.getByRole('button', { name: `查看“${targetLabel}”的结果`, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    await page.waitForFunction(() => {
      const dialog = document.querySelector('[role="dialog"]');
      return dialog && getComputedStyle(dialog).opacity === '1' && !dialog.getAnimations({ subtree: true }).some(animation => animation.playState === 'running');
    });
    assert.match(await dialog.innerText(), /固定验收：理解一句问候/);
    if (inspectReceipts) {
      assert.match(await dialog.innerText(), /已恢复 2 份指纹一致的完整候选并重新校验/);
      assert.match(await dialog.innerText(), /原调用耗时、Token 和费用未知/);
      await dialog.getByText('已恢复 2 份指纹一致的完整候选并重新校验；原调用耗时、Token 和费用未知。', { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(artifacts, 'brief-receipt-note.png'), fullPage: true, animations: 'disabled' });
      await dialog.locator('.result-debug > summary').click();
      assert.match(await dialog.innerText(), /候选恢复来源（不是模型调用记录）/);
      assert.equal(await dialog.locator('.debug-section').filter({ hasText: '候选恢复来源' }).locator('article').count(), 2);
      for (const label of ['模型调用', '输入 Token', '输出 Token', '其中思考', '模型费用']) {
        const metric = dialog.locator('.debug-metrics > div').filter({ has: page.locator('dt', { hasText: label }) });
        assert.equal(await metric.locator('dd').innerText(), '未知');
      }
      await dialog.locator('.debug-section').filter({ hasText: '候选恢复来源' }).scrollIntoViewIfNeeded();
    }
    await page.screenshot({ path: resolve(artifacts, `brief-history-${expectedCount}.png`), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '关闭步骤结果' }).click();
  }
  await inspectHistory(1, false);
  const second = await api(prefix + '/operations/localization', request);
  assert.notEqual(second.operation_id, first.operation_id);
  await poll(() => api('/__brief_acceptance/state'), value => value.model_calls === 3);
  await page.locator('.task-row.running').filter({ hasText: '本土化' }).waitFor();
  assert.equal((await api(prefix + `/operations/${first.operation_id}`)).status, 'failed');
  await page.screenshot({ path: resolve(artifacts, 'brief-recovery-running.png'), fullPage: true });
  await api('/__brief_acceptance/release', { phase: 'recovery' });
  const secondDone = await poll(() => api(prefix + `/operations/${second.operation_id}`), op => ['success', 'failed'].includes(op.status));
  assert.equal(secondDone.status, 'success', JSON.stringify(secondDone.result_summary));
  const afterRecovery = await api('/__brief_acceptance/state');
  assert.equal(afterRecovery.model_calls, 3);
  assert.equal(afterRecovery.outline_calls, 1);
  assert.equal(afterRecovery.detail_calls, 2);
  assert.equal(afterRecovery.journal.length, 3);
  assert.deepEqual(afterRecovery.call_order, ['outline', 'details', 'details']);
  assert.equal(afterRecovery.journal.find(record => record.call_error_code === 'codex_cli_rate_limited').file_sha256, rejected.file_sha256);
  assert.equal(afterRecovery.journal.find(record => record.batch_id === 'brief-outline').candidate_fingerprint, outline.candidate_fingerprint);
  const recovered = afterRecovery.journal.find(record => record.recovery_ordinal === 1);
  assert.equal(recovered.status, 'validation_passed');
  assert.equal(recovered.recovery_execution_id, second.operation_id);
  assert.equal(recovered.input_fingerprint, rejected.input_fingerprint);
  assert.equal(recovered.batch_id, rejected.batch_id);
  assert.equal(afterRecovery.journal.find(record => record.batch_id === 'brief-outline').validation_count, 2);
  await inspectHistory(2, true);
  const third = await api(prefix + '/operations/localization', request);
  assert.equal(new Set([first.operation_id, second.operation_id, third.operation_id]).size, 3);
  const thirdDone = await poll(() => api(prefix + `/operations/${third.operation_id}`), op => ['success', 'failed'].includes(op.status));
  assert.equal(thirdDone.status, 'success', JSON.stringify(thirdDone.result_summary));
  const after = await api('/__brief_acceptance/state');
  assert.equal(after.model_calls, 3);
  assert.equal(after.journal.length, 3);
  assert.equal(after.journal.find(record => record.call_error_code === 'codex_cli_rate_limited').file_sha256, rejected.file_sha256);
  assert.equal(after.journal.find(record => record.batch_id === 'brief-outline').validation_count, 3);
  assert.equal(after.journal.find(record => record.recovery_ordinal === 1).validation_count, 2);
  assert.equal(after.journal.find(record => record.recovery_ordinal === 1).candidate_fingerprint, recovered.candidate_fingerprint);
  const stillFailed = await api(prefix + `/operations/${first.operation_id}`);
  assert.equal(stillFailed.status, 'failed');
  assert.equal(stillFailed.error_message, firstDone.error_message);
  assert.deepEqual(stillFailed.result_summary, firstDone.result_summary);
  await inspectHistory(3, true);
  const imported = await api('/__brief_acceptance/import-candidates', {});
  assert.equal(imported.receipts.length, 2);
  assert.ok(imported.receipts.every(receipt => receipt.telemetry === 'unavailable'));
  const fourth = await api(prefix + '/operations/localization', { ...request,
    development_session_id: state.receipt_session_id, recover_verified_candidates: true });
  const fourthDone = await poll(() => api(prefix + `/operations/${fourth.operation_id}`), op => ['success', 'failed'].includes(op.status));
  assert.equal(fourthDone.status, 'success', JSON.stringify(fourthDone.result_summary));
  const afterImport = await api('/__brief_acceptance/state');
  assert.equal(afterImport.model_calls, 3, 'Receipt recovery must not invoke the fixed provider');
  assert.deepEqual(afterImport.journal, after.journal, 'Import must not rewrite the original session');
  assert.equal(afterImport.receipt_journal.length, 2);
  for (const record of afterImport.receipt_journal) {
    assert.equal(record.status, 'validation_passed');
    assert.equal(record.recorded_call_count, 0, 'Do not fabricate historical call telemetry');
    assert.equal(record.recovered_candidate.telemetry, 'unavailable');
    assert.ok(imported.receipts.some(receipt => receipt.receipt_fingerprint === record.recovered_candidate.receipt_fingerprint));
  }
  await inspectHistory(4, true, true);
  await inspectHistory(4, true, true); // Refresh preserves both provenance and unknown telemetry.
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ briefJournalWeb: 'passed', operationIds: [first.operation_id, second.operation_id, third.operation_id, fourth.operation_id],
    modelCalls: after.model_calls, journalValidations: after.journal.map(record => record.validation_count),
    importedCandidateCount: imported.receipts.length, receiptRecoveryProviderCalls: 0, historicalTelemetry: 'unavailable',
    originalRejectionUnchanged: true, completedOutlineCalls: after.outline_calls, rejectedDetailCalls: after.detail_calls,
    entry: 'public API explicit development recovery; real SPA running/failed-history/success-detail/refresh', consoleErrors: errors }));
  }
} catch (error) {
  console.error(JSON.stringify({ browserFailure: String(error), body: (await page.locator('body').innerText()).slice(-7000), consoleErrors: errors }));
  throw error;
} finally {
  await browser.close();
}
