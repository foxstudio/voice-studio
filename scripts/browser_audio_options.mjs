// Explicit test-only hardware boundary. Never used by the application/browser UI.
// Chromium's AUDIO_FAKE still consumes decoded audio on a running clock:
// https://chromium.googlesource.com/chromium/src/+/HEAD/media/audio/audio_manager_base.cc
export function browserAudioOptions(mode = process.env.VOICE_STUDIO_BROWSER_AUDIO ?? 'native') {
  if (mode === 'native') return {};
  if (mode === 'fake') return { args: ['--disable-audio-output'] };
  throw new Error('VOICE_STUDIO_BROWSER_AUDIO must be native or fake');
}

export function browserAudioEvidence() {
  console.log(JSON.stringify({ browserAudioOutput: process.env.VOICE_STUDIO_BROWSER_AUDIO ?? 'native',
    scope: 'browser playback logic; not a listening acceptance' }));
}
