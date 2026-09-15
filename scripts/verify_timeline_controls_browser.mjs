import assert from 'node:assert/strict';
import { resolve } from 'node:path';

// Called only by the isolated dubbing harness, never against the user's project.
export async function verifyTimelineControls(page, base, projectId, artifactDir) {
  assert.ok(![5173, 18000].includes(Number(new URL(base).port)));
  const prefix = `${base}/api/projects/${projectId}/video-localization`;
  const read = async () => (await (await page.request.get(prefix)).json());
  const observedSaves = [];
  const observeSave = request => {
    if (request.method() === 'PATCH' && request.url().startsWith(prefix)) {
      observedSaves.push({ path: new URL(request.url()).pathname.split('/').pop(), body: request.postDataJSON() });
    }
  };
  page.on('request', observeSave);
  const solo = page.getByRole('button', { name: '独奏合成配音轨 1', exact: true });
  const mute = page.getByRole('button', { name: '静音合成配音轨 1', exact: true });
  const before = await read();
  const first = before.timeline_clips.find(clip => clip.track_id === 'dub' && (clip.dub_lane ?? 0) === 0);
  assert.ok(first);
  const clip = page.locator(`[data-audio-clip-id="${first.clip_id}"]`);
  const savesSolo = (response, value) => {
    if (response.request().method() !== 'PATCH' || !new URL(response.url()).pathname.endsWith('/ui-state')) return false;
    const patch = response.request().postDataJSON();
    return patch?.dub_lane_states?.['0']?.solo === value;
  };
  await clip.scrollIntoViewIfNeeded();
  const box = await clip.boundingBox();
  const saved = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname.endsWith('/timeline-edit'), { timeout: 20000 });
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 18, box.y + box.height / 2, { steps: 6 });
  await page.mouse.up();
  // A clip edit is pending when the user changes the mixer: both must save.
  const mixerSaved = page.waitForResponse(response => savesSolo(response, true), { timeout: 20000 }).catch(async error => {
    console.error(JSON.stringify({ mixerSaveDiagnostics: observedSaves,
      persistedLane: (await read()).ui_state.dub_lane_states?.['0'],
      pageStatus: await page.locator('.cutting-head').innerText() }));
    throw error;
  });
  await solo.click();
  assert.equal(await solo.getAttribute('aria-pressed'), 'true');
  await page.getByRole('button', { name: '跳到视频开始', exact: true }).click();
  const startTime = await page.locator('.time-current').innerText();
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await page.getByRole('button', { name: '暂停', exact: true }).waitFor();
  await page.waitForTimeout(3000);
  assert.notEqual(await page.locator('.time-current').innerText(), startTime, 'Playback must advance');
  assert.equal(await solo.getAttribute('aria-pressed'), 'true', 'Playback must not cancel solo');
  const pause = page.getByRole('button', { name: '暂停', exact: true });
  if (await pause.count()) {
    await pause.click({ timeout: 1000 }).catch(async error => {
      // Short fixtures can naturally finish between the count and click.
      if (!(await page.getByRole('button', { name: '播放', exact: true }).count())) throw error;
    });
  }
  assert.ok((await saved).ok());
  assert.ok((await mixerSaved).ok());
  page.off('request', observeSave);
  const persisted = await read();
  assert.equal(persisted.ui_state.dub_lane_states?.['0']?.solo, true,
    'Compact clip save must not silently discard the queued solo change');
  assert.equal(await solo.getAttribute('aria-pressed'), 'true');
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await solo.getAttribute('aria-pressed'), 'true', 'Solo must survive refresh');

  // A delayed acknowledgement must not undo a newer click.
  let release;
  let captured;
  const capturedPromise = new Promise(resolve => { captured = resolve; });
  await page.route('**/video-localization/ui-state', async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    const response = await route.fetch();
    captured();
    await new Promise(resolve => { release = resolve; });
    await route.fulfill({ response });
  }, { times: 1 });
  const staleUiSaved = page.waitForResponse(response => savesSolo(response, false), { timeout: 20000 });
  await solo.click();
  await capturedPromise;
  const latestUiSaved = page.waitForResponse(response => savesSolo(response, true), { timeout: 20000 });
  await solo.click();
  release();
  assert.ok((await staleUiSaved).ok());
  assert.equal(await solo.getAttribute('aria-pressed'), 'true', 'Old save response reverted a newer solo choice');
  // Flush through the real manual-save barrier instead of depending on the
  // autosave debounce landing inside a busy full-suite timeout window.
  await page.keyboard.press('Meta+s');
  assert.ok((await latestUiSaved).ok());
  assert.equal(await solo.getAttribute('aria-pressed'), 'true', 'Old save response reverted a newer solo choice');
  await page.waitForFunction(() => document.body.innerText.includes('已保存'), { timeout: 20000 });
  assert.equal((await read()).ui_state.dub_lane_states?.['0']?.solo, true);

  const style = () => solo.evaluate(element => {
    const css = getComputedStyle(element);
    return { background: css.backgroundColor, shadow: css.boxShadow, outline: css.outlineStyle,
      transform: css.transform, transition: css.transitionDuration };
  });
  await page.mouse.move(0, 0);
  await page.waitForTimeout(150);
  const selected = await style();
  await solo.hover();
  await page.waitForTimeout(150);
  assert.notEqual((await style()).background, selected.background, 'Selected hover needs its own feedback');
  assert.notEqual((await style()).shadow, 'none', 'Hover must retain the selected marker');
  await page.mouse.down();
  await page.waitForTimeout(100);
  assert.notEqual((await style()).transform, 'none', 'Pointer down should give brief press feedback');
  await page.mouse.move(0, 0);
  await page.mouse.up();
  await solo.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  assert.equal((await style()).outline, 'solid');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  assert.equal((await style()).transition, '0s');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await mute.click();
  assert.equal(await mute.getAttribute('aria-pressed'), 'true');
  assert.equal(await solo.getAttribute('aria-pressed'), 'false', 'Mute and solo remain mutually exclusive');
  for (const width of [760, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await mute.scrollIntoViewIfNeeded();
    const target = await mute.boundingBox();
    assert.ok(target.width >= 24 && target.height >= 24);
    await page.screenshot({ path: resolve(artifactDir, `timeline-controls-${width}.png`), fullPage: true });
  }
  await page.waitForFunction(() => document.body.innerText.includes('已保存'), { timeout: 20000 });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await mute.getAttribute('aria-pressed'), 'true');
  assert.equal(await solo.getAttribute('aria-pressed'), 'false');
  console.log(JSON.stringify({ timeline_controls: 'passed', checks: [
    'clip edit plus solo saved', 'late UI acknowledgement', 'refresh persistence',
    'mute solo exclusion', 'selected hover press focus', 'reduced motion', 'narrow and wide targets'
  ] }));
}
