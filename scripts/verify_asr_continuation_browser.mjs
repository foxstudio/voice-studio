#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';

import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.ASR_CONTINUATION_E2E_URL;
if (
  !base
  || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base)
  || [5173, 18000, 8000].includes(Number(new URL(base).port))
) {
  throw new Error('An isolated non-production URL is required');
}
const playwright = await import(
  pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href
);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const browser = await chromium.launch(isolatedBrowserOptions(
  existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    ? { channel: 'chrome' }
    : {},
));
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') errors.push(message.text());
});

async function api(path, data) {
  const response = data === undefined
    ? await page.request.get(base + '/api' + path)
    : await page.request.post(base + '/api' + path, { data });
  const payload = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(payload));
  return payload;
}

async function poll(action, condition, failure) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const value = await action();
    if (condition(value)) return value;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 100));
  }
  throw new Error(failure);
}

async function findHistoryRow() {
  const taskCenter = page.locator('section[aria-label="后台任务进度"]');
  const rows = taskCenter.locator('article.history-row');
  await rows.first().waitFor();
  const row = rows.first();
  await row.waitFor();
  return row;
}

async function inspectPersistedHistory(state, operationId) {
  await page.reload({ waitUntil: 'networkidle' });
  const refreshed = await api(
    `/projects/${state.project_id}/video-localization`,
  );
  assert.equal(refreshed.cues.length, 1);
  assert.equal(refreshed.cues[0].cue_id, 'user-cue');
  assert.equal(refreshed.cues[0].en_subtitle_text, 'user edit must survive');
  const persisted = refreshed.operations.find(
    operation => operation.operation_id === operationId,
  );
  assert.equal(persisted?.status, 'success', JSON.stringify(persisted));

  await page.getByRole('button', { name: '任务', exact: true }).click();
  const row = await findHistoryRow();
  assert.equal(await row.count(), 1);
  if (await row.locator('.history-summary').getAttribute('aria-expanded') !== 'true') {
    await row.locator('.history-summary').click();
  }
  await row.getByText('处理流程', { exact: true }).waitFor();
  assert.match(await row.innerText(), /本地收尾检查/);
  const resultButton = row.getByRole('button', {
    name: '查看“本地收尾检查”的结果',
    exact: true,
  });
  await resultButton.click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, /已使用直接前驱的新结果构建输入并运行/);
  await dialog.getByRole('button', { name: '关闭步骤结果' }).click();
}

try {
  const state = await api('/__asr_continuation_acceptance/state');
  const prefix = `/projects/${state.project_id}/video-localization`;
  await page.goto(
    `${base}/video-localization?project_id=${state.project_id}`,
    { waitUntil: 'networkidle' },
  );
  await page.getByRole('button', { name: '任务', exact: true }).click();

  const operation = await api(prefix + '/operations/english-asr', {
    execution_mode: 'development_target',
    development_source_operation_id: 'formal-op',
    development_predecessor_operation_id: 'review-op',
    development_target_step_id: 'whole_recheck_r1',
  });
  const started = await poll(
    () => api('/__asr_continuation_acceptance/state'),
    current => current.whole_recheck_started === true,
    'Fixed whole recheck did not start',
  );
  assert.equal(started.model_calls, 0);
  assert.equal(started.whole_recheck_calls, 1);

  const activeRows = page.locator('.active-task-list article.task-row');
  await activeRows.first().waitFor();
  assert.equal(await activeRows.count(), 1);
  const active = activeRows.filter({ hasText: '本地收尾检查' });
  await active.waitFor();
  assert.match(
    await active.innerText(),
    /从人声轨生成 ASR 字幕[\s\S]*处理中[\s\S]*本地收尾检查/,
  );
  await api('/__asr_continuation_acceptance/release', {});

  const completed = await poll(
    () => api(prefix + `/operations/${operation.operation_id}`),
    current => ['success', 'failed'].includes(current.status),
    'ASR continuation operation did not settle',
  );
  assert.equal(completed.status, 'success', JSON.stringify(completed));
  assert.equal(
    completed.result_summary.stage,
    'ASR 开发结果已保存，未写入正式字幕',
  );
  assert.equal(
    completed.result_summary.task_step_results.whole_recheck_r1.summary,
    '已使用直接前驱的新结果构建输入并运行。',
  );
  assert.deepEqual(
    completed.result_summary.task_stage_groups
      .flatMap(stage => stage.atomic_tasks)
      .map(task => task.id),
    ['whole_recheck_r1'],
  );

  const after = await api('/__asr_continuation_acceptance/state');
  assert.equal(after.model_calls, 0);
  assert.equal(after.whole_recheck_calls, 1);
  await inspectPersistedHistory(state, operation.operation_id);
  await inspectPersistedHistory(state, operation.operation_id);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    asrContinuationWeb: 'passed',
    operationId: operation.operation_id,
    targetStep: 'whole_recheck_r1',
    runningStateVisible: true,
    historyVisible: true,
    refreshChecks: 2,
    formalCuePreserved: true,
    modelCalls: after.model_calls,
    consoleErrors: errors,
  }));
} catch (error) {
  console.error(JSON.stringify({
    browserFailure: String(error),
    body: (await page.locator('body').innerText()).slice(-5000),
    consoleErrors: errors,
  }));
  throw error;
} finally {
  await browser.close();
}
