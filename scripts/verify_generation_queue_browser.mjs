#!/usr/bin/env node
import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
// Called only by verify_generation_queue_web.py; never targets a default port.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const base = process.env.GENERATION_QUEUE_E2E_URL;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || [5173, 18000].includes(Number(new URL(base).port))) {
  throw new Error('An isolated, non-production loopback URL is required');
}
const playwright = await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium = playwright.chromium ?? playwright.default?.chromium;
const installedChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch(isolatedBrowserOptions(existsSync(installedChrome) ? { channel: 'chrome' } : {}));
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
const api = async (path, body) => {
  const response = await page.request.fetch(`${base}/api${path}`, body === undefined ? {} : { method: 'POST', data: body });
  assert.equal(response.ok(), true, `${path}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const waitFor = async (predicate, label) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 80));
  }
  throw new Error(`Timed out: ${label}`);
};
const card = (text) => page.locator('article.result-card').filter({ has: page.locator('strong.result-title', { hasText: text }) });
const release = (text) => api('/__queue_acceptance/release', { text });
const started = async () => (await api('/__queue_acceptance/state')).events.filter((event) => event.event === 'started').map((event) => event.text);

try {
  // Public API submission preserves real task normalization and scheduling.
  const projectA = await api('/projects', { name: '隔离队列验收甲', default_engine_id: 'omnivoice' });
  const projectB = await api('/projects', { name: '隔离队列验收乙', default_engine_id: 'omnivoice' });
  const submit = async (text, project, priority = 'normal') => api('/generate', {
    text, engine_id: 'omnivoice', project_id: project.project_id,
    resource_priority: priority, output_format: 'wav', language: 'zh',
  });
  const runningText = '验收甲正在生成';
  const running = await submit(runningText, projectA);
  await waitFor(async () => (await started()).includes(runningText), 'first provider entry');
  const a1 = await submit('验收甲普通第一条', projectA);
  const a2 = await submit('验收甲普通第二条', projectA);
  const foreground = await submit('验收乙优先恢复', projectB, 'foreground_resume');
  const b1 = await submit('验收乙普通第一条', projectB);
  const cancelled = await submit('验收乙等待取消', projectB);

  await page.goto(`${base}/generate`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '生成记录', exact: true }).waitFor();
  await card(runningText).locator('.result-status').filter({ hasText: /生成|运行|渲染/ }).waitFor();
  await card('验收乙等待取消').locator('.result-status').filter({ hasText: /排队|等待/ }).waitFor();
  await card('验收乙等待取消').getByRole('button', { name: '取消任务', exact: true }).click();
  await waitFor(async () => (await api(`/tasks/${cancelled.task_id}`)).status === 'cancelled', 'public UI cancel');
  await card('验收乙等待取消').locator('.result-status').filter({ hasText: /取消/ }).waitFor();
  if (process.env.GENERATION_QUEUE_E2E_CHECK_QUEUE_COPY !== '0') {
    const queueText = await page.locator('#records').innerText();
    assert.doesNotMatch(queueText, /按创建时间|等待队列第|排队\s+\d+|前面还有\s*\d+\s*条|下一条是|完成后会轮到这一条/);
    assert.match(queueText, /按项目与优先级调度/);
  }
  await page.screenshot({ path: resolve(process.env.GENERATION_QUEUE_E2E_ARTIFACT_DIR, 'queue-running.png') });

  const expected = [runningText, '验收乙优先恢复', '验收甲普通第一条', '验收乙普通第一条', '验收甲普通第二条'];
  for (let index = 0; index < expected.length; index += 1) {
    await waitFor(async () => (await started()).length === index + 1, `provider entry ${index + 1}`);
    assert.deepEqual(await started(), expected.slice(0, index + 1));
    await release(expected[index]);
  }
  const tasks = [running, foreground, a1, b1, a2];
  await waitFor(async () => (await Promise.all(tasks.map((task) => api(`/tasks/${task.task_id}`)))).every((task) => task.status === 'success'), 'all real tasks persist success');
  assert.equal((await started()).includes('验收乙等待取消'), false, 'cancelled waiter must never enter provider');

  await page.getByRole('button', { name: '刷新列表', exact: true }).click();
  for (const text of expected) {
    await card(text).locator('.result-status').filter({ hasText: '成功' }).waitFor();
    await card(text).getByRole('button', { name: '播放音频', exact: true }).waitFor();
  }
  // Inspect a real result parameter popup and fetch/play the persisted WAV.
  await card(runningText).getByRole('button', { name: '查看生成参数', exact: true }).hover();
  await card(runningText).getByText('生成参数', { exact: true }).waitFor();
  const audioUrl = await card(runningText).getByRole('link', { name: '下载音频', exact: true }).getAttribute('href');
  const audioResponse = await page.request.get(new URL(audioUrl, base).href);
  assert.equal(audioResponse.status(), 200);
  const wav = await audioResponse.body();
  assert.equal(wav.subarray(0, 4).toString(), 'RIFF');
  await card(runningText).getByRole('button', { name: '播放音频', exact: true }).click();
  await card(runningText).getByRole('button', { name: '停止播放', exact: true }).waitFor();
  await page.reload({ waitUntil: 'networkidle' });
  for (const text of expected) await card(text).locator('.result-status').filter({ hasText: '成功' }).waitFor();
  await card('验收乙等待取消').locator('.result-status').filter({ hasText: /取消/ }).waitFor();
  // A real browser-button submission additionally exercises the ordinary UI.
  await card(runningText).getByRole('button', { name: '复用参数', exact: true }).click();
  const uiText = '验收网页按钮生成';
  await waitFor(async () => (await page.getByPlaceholder('输入要合成的文本', { exact: true }).inputValue()) === runningText, 'reuse parameters finishes');
  await page.getByPlaceholder('输入要合成的文本', { exact: true }).fill(uiText);
  await page.getByPlaceholder('输入要合成的文本', { exact: true }).blur();
  const submitted = page.waitForResponse((response) => response.url() === `${base}/api/generate` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '生成', exact: true }).click();
  const uiResponse = await submitted;
  assert.equal(uiResponse.ok(), true);
  assert.equal(uiResponse.request().postDataJSON().text, uiText);
  const uiTask = await uiResponse.json();
  await waitFor(async () => (await started()).includes(uiText), 'UI provider entry');
  await release(uiText);
  await waitFor(async () => (await api(`/tasks/${uiTask.task_id}`)).status === 'success', 'UI task success');
  await page.reload({ waitUntil: 'networkidle' });
  await card(uiText).locator('.result-status').filter({ hasText: '成功' }).waitFor();
  await page.screenshot({ path: resolve(process.env.GENERATION_QUEUE_E2E_ARTIFACT_DIR, 'queue-refreshed.png') });
  assert.deepEqual(errors, [], 'no browser page errors');
  console.log(JSON.stringify({ status: 'passed', projects: 2, completed: tasks.length + 1, cancelledWithoutProviderEntry: true, dispatchOrder: expected, browser: ['running', 'queued', 'cancel', 'history', 'parameters', 'WAV', 'playback', 'refresh', 'generate button'], providerBoundary: 'fixed OmniVoice WAV; ASR unavailable; no model invoked' }));
} catch (error) {
  console.error((await page.locator('body').innerText()).slice(-10000));
  console.error(JSON.stringify((await api('/tasks/page?limit=20')).items.map(({ input_text, status, error_message }) => ({ input_text, status, error_message }))));
  throw error;
} finally {
  await browser.close();
}
