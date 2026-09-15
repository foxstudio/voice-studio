#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';

import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.LOCALIZATION_TERMINAL_E2E_URL;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000, 8000].includes(Number(new URL(base).port))) {
  throw new Error('An isolated non-production URL is required');
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
  const response = data === undefined
    ? await page.request.get(base + '/api' + path)
    : await page.request.post(base + '/api' + path, { data });
  const payload = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(payload));
  return payload;
}

async function poll(action, condition) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const value = await action();
    if (condition(value)) return value;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 100));
  }
  throw new Error('Formal localization operation did not settle');
}

async function inspectPersistedHistory(state, expectedSummary) {
  await page.reload({ waitUntil: 'networkidle' });
  const refreshedDraft = await api(`/projects/${state.project_id}/video-localization`);
  assert.equal(refreshedDraft.localized_spoken_segments.length, state.spoken_count);
  assert.equal(refreshedDraft.localized_subtitles.length, state.subtitle_count);
  const localizedTrack = page.locator('[data-track-id="localizedSubtitles"]');
  const visibleSubtitles = localizedTrack.locator('[data-subtitle-item-id]');
  await visibleSubtitles.first().waitFor();
  assert.equal(await visibleSubtitles.count(), state.subtitle_count);
  const firstSubtitle = refreshedDraft.localized_subtitles[0];
  const firstVisibleSubtitle = localizedTrack.locator(
    `[data-subtitle-item-id="${firstSubtitle.subtitle_id}"]`,
  );
  assert.match(await firstVisibleSubtitle.innerText(), new RegExp(firstSubtitle.text));
  await firstVisibleSubtitle.click();
  await page.locator('.inspector-mode-tabs').getByRole('button', {
    name: '字幕',
    exact: true,
  }).click();
  const subtitleEditors = page.locator('.subtitle-textarea');
  assert.equal(await subtitleEditors.nth(0).inputValue(), firstSubtitle.text);
  assert.equal(
    await subtitleEditors.nth(1).inputValue(),
    firstSubtitle.tts_text || firstSubtitle.text,
  );
  await page.getByRole('button', { name: '任务', exact: true }).click();
  const row = page.locator('.history-row').filter({ hasText: '本土化' });
  await row.waitFor();
  assert.equal(await row.count(), 1);
  if (await row.locator('.history-summary').getAttribute('aria-expanded') !== 'true') {
    await row.locator('.history-summary').click();
  }
  const resultButton = row.getByRole('button', {
    name: '查看“保存正式本土化双轨”的结果',
    exact: true,
  });
  await resultButton.click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, new RegExp(expectedSummary.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(text, /原文时间[\s\S]*未修改/);
  await page.getByRole('button', { name: '关闭步骤结果' }).click();

}

try {
  const state = await api('/__localization_terminal_acceptance/state');
  const prefix = `/projects/${state.project_id}/video-localization`;
  await page.goto(`${base}/video-localization?project_id=${state.project_id}`, { waitUntil: 'networkidle' });
  const operationResponse = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api${prefix}/operations/localization`
  ));
  await page.getByRole('button', { name: '生成本土化字幕', exact: true }).click();
  const submitted = await operationResponse;
  const operation = await submitted.json();
  assert.equal(submitted.status(), 200, JSON.stringify(operation));
  const completed = await poll(
    () => api(prefix + `/operations/${operation.operation_id}`),
    current => ['success', 'failed'].includes(current.status),
  );
  assert.equal(completed.status, 'success', JSON.stringify(completed));
  assert.equal(completed.result_summary.task_final_result.status, 'success');
  const commitResult = completed.result_summary.task_step_results.commit_localization_tracks;
  assert.equal(commitResult.status, 'success');
  const expectedSummary = `已保存 ${state.spoken_count} 段中文台词和 ${state.subtitle_count} 条上屏字幕。`;
  assert.equal(commitResult.summary, expectedSummary);

  const after = await api('/__localization_terminal_acceptance/state');
  assert.equal(after.provider_calls, 0);
  assert.equal(after.post_commit_checkpoint_failures, 1);
  await inspectPersistedHistory(state, expectedSummary);
  await inspectPersistedHistory(state, expectedSummary);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    localizationTerminalWeb: 'passed',
    operationId: operation.operation_id,
    formalTracks: { spoken: state.spoken_count, subtitles: state.subtitle_count },
    taskStatus: completed.status,
    checkpointFailureAfterCommit: true,
    refreshChecks: 2,
    entry: 'production SPA localization button',
    modelCalls: after.provider_calls,
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
