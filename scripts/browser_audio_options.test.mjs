import assert from 'node:assert/strict';
import test from 'node:test';
import { browserAudioOptions } from './browser_audio_options.mjs';

test('native output is unchanged', () => assert.deepEqual(browserAudioOptions('native'), {}));
test('fake output is an explicit browser-only option', () =>
  assert.deepEqual(browserAudioOptions('fake'), { args: ['--disable-audio-output'] }));
test('unknown output policy fails closed', () => assert.throws(() => browserAudioOptions('typo')));

// Isolation must preserve each harness's audio and scrollbar settings.
test('browser isolation preserves caller settings without mutating shared options', async () => {
  const { isolatedBrowserOptions } = await import('./isolated_browser_options.mjs');
  const original = { channel: 'chrome', args: ['--disable-audio-output'], ignoreDefaultArgs: ['--hide-scrollbars'] };
  const first = isolatedBrowserOptions(original);
  assert.deepEqual(original.args, ['--disable-audio-output']);
  assert.equal(first.channel, 'chrome');
  assert.deepEqual(first.ignoreDefaultArgs, ['--hide-scrollbars']);
  assert.ok(first.args.includes('--disable-audio-output'));
  assert.equal(isolatedBrowserOptions(first).args.filter(arg => arg === '--disable-updater-scheduler').length, 1);
});
