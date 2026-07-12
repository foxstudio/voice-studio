import { describe, expect, it } from 'vitest';
import { defaultTrackStates, resolveTrackStates } from './studio-state';

describe('video localization track state', () => {
	it('starts with the original track soloed and subtitles collapsed', () => {
		const states = defaultTrackStates();
		expect(states.original.solo).toBe(true);
		expect(states.subtitles.collapsed).toBe(true);
	});

	it('preserves positive track gain up to +6 dB', () => {
		const states = resolveTrackStates({
			original: { volume: 2 },
			vocals: { volume: 8 }
		});
		expect(states.original.volume).toBe(2);
		expect(states.vocals.volume).toBe(2);
	});

	it('keeps mute and solo mutually exclusive while restoring all-track playback when solo is absent', () => {
		const states = resolveTrackStates({
			original: { muted: true, solo: false },
			vocals: { muted: true, solo: true }
		});
		expect(states.original).toMatchObject({ muted: true, solo: false });
		expect(states.vocals).toMatchObject({ muted: false, solo: true });
	});
});
