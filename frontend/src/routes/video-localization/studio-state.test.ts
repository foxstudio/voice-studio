import { describe, expect, it } from 'vitest';
import { defaultAudioTrackOrder, defaultTrackStates, extendSubtitleCuesAcrossShortGaps, reorderAudioTracks, resolveAudioTrackOrder, resolveTrackStates, subtitleCueDragBounds, timelineViewportRange, timeRangeIntersectsViewport } from './studio-state';

describe('video localization track state', () => {
	it('starts with the original track soloed and no legacy collapse state', () => {
		const states = defaultTrackStates();
		expect(states.original.solo).toBe(true);
		expect(states.subtitles).not.toHaveProperty('collapsed');
		expect(states.localizedSubtitles).not.toHaveProperty('collapsed');
	});

	it('preserves positive track gain up to +12 dB', () => {
		const states = resolveTrackStates({
			original: { volume: 2 },
			vocals: { volume: 8 }
		});
		expect(states.original.volume).toBe(2);
		expect(states.vocals.volume).toBe(4);
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

describe('timeline viewport range', () => {
	it('returns the visible time window with bounded overscan', () => {
		expect(timelineViewportRange(60_000, 4, 900, 900)).toEqual({ startMs: 7_500, endMs: 37_500 });
	});

	it('detects ranges that intersect the visible window', () => {
		const viewport = { startMs: 10_000, endMs: 20_000 };
		expect(timeRangeIntersectsViewport(9_000, 10_000, viewport)).toBe(true);
		expect(timeRangeIntersectsViewport(20_000, 21_000, viewport)).toBe(true);
		expect(timeRangeIntersectsViewport(21_000, 22_000, viewport)).toBe(false);
	});
});

describe('video localization audio track order', () => {
	it('defaults to original, vocals, dub, then background', () => {
		expect(defaultAudioTrackOrder()).toEqual(['original', 'vocals', 'dub', 'background']);
	});

	it('filters invalid entries and fills missing tracks', () => {
		expect(resolveAudioTrackOrder(['dub', 'vocals', 'dub', 'unknown'])).toEqual(['dub', 'vocals', 'original', 'background']);
	});

	it('moves an audio track before the drop target without including subtitles', () => {
		expect(reorderAudioTracks(defaultAudioTrackOrder(), 'background', 'vocals')).toEqual(['original', 'background', 'vocals', 'dub']);
	});

	it('can move an audio track to the final position', () => {
		expect(reorderAudioTracks(defaultAudioTrackOrder(), 'original', 'background', 'after')).toEqual(['vocals', 'dub', 'background', 'original']);
	});
});

describe('subtitle cue drag bounds', () => {
	it('uses adjacent cue edges as hard limits', () => {
		expect(subtitleCueDragBounds([
			{ cue_id: 'cue_1', start_ms: 0, end_ms: 1000 },
			{ cue_id: 'cue_2', start_ms: 1300, end_ms: 2200 },
			{ cue_id: 'cue_3', start_ms: 2500, end_ms: 3200 }
		], 'cue_2', 5000)).toEqual({ minStartMs: 1000, maxEndMs: 2500 });
	});

	it('uses timeline edges for the first and last cues', () => {
		const cues = [
			{ cue_id: 'cue_1', start_ms: 100, end_ms: 900 },
			{ cue_id: 'cue_2', start_ms: 1200, end_ms: 2000 }
		];
		expect(subtitleCueDragBounds(cues, 'cue_1', 4000)).toEqual({ minStartMs: 0, maxEndMs: 1200 });
		expect(subtitleCueDragBounds(cues, 'cue_2', 4000)).toEqual({ minStartMs: 900, maxEndMs: 4000 });
	});
});

describe('short subtitle gap extension', () => {
	it('extends only short positive gaps and leaves the final cue unchanged', () => {
		const cues = extendSubtitleCuesAcrossShortGaps([
			{ cue_id: 'cue_1', start_ms: 0, end_ms: 1000 },
			{ cue_id: 'cue_2', start_ms: 1320, end_ms: 2200 },
			{ cue_id: 'cue_3', start_ms: 3000, end_ms: 3600 }
		]);
		expect(cues.map((cue) => cue.end_ms)).toEqual([1320, 2200, 3600]);
	});

	it('does not change overlaps or mutate the source array', () => {
		const source = [
			{ cue_id: 'cue_1', start_ms: 0, end_ms: 1100 },
			{ cue_id: 'cue_2', start_ms: 1000, end_ms: 1800 }
		];
		const cues = extendSubtitleCuesAcrossShortGaps(source);
		expect(cues[0].end_ms).toBe(1100);
		expect(source[0].end_ms).toBe(1100);
	});
});
