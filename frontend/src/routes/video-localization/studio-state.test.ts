import { describe, expect, it } from 'vitest';
import { defaultAudioTrackOrder, defaultTrackStates, extendSubtitleCuesToFollowingStart, reorderAudioTracks, resolveAudibleMix, resolveAudioTrackOrder, resolveDubLaneStates, resolveTrackStates, subtitleCueDragBounds, timelineViewportRange, timeRangeIntersectsViewport } from './studio-state';

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

	it('resolves dub lane controls independently while keeping lane zero compatible', () => {
		const legacyDub = resolveTrackStates({ dub: { muted: true, volume: 0.6, locked: true } }).dub;
		const lanes = resolveDubLaneStates({
			'1': { solo: true, volume: 1.4 },
			'3': { muted: true, label: '补录' }
		}, 2, legacyDub);

		expect(lanes['0']).toMatchObject({ muted: true, volume: 0.6, locked: true });
		expect(lanes['1']).toMatchObject({ muted: false, solo: true, volume: 1.4, locked: false });
		expect(lanes['3']).toBeUndefined();
	});

	it('drops stale control state after the last clip leaves an additional lane', () => {
		const legacyDub = resolveTrackStates({ dub: { label: '主配音' } }).dub;
		const lanes = resolveDubLaneStates({
			'0': { label: '主配音' },
			'1': { label: '临时补录', muted: true }
		}, 1, legacyDub);

		expect(Object.keys(lanes)).toEqual(['0']);
		expect(lanes['0'].label).toBe('主配音');
	});

	it('inherits the legacy lane-zero solo state', () => {
		const legacyDub = resolveTrackStates({ dub: { solo: true, volume: 0.8 } }).dub;
		const lanes = resolveDubLaneStates({}, 1, legacyDub);

		expect(lanes['0']).toMatchObject({ muted: false, solo: true, volume: 0.8 });
	});

	it('does not let an empty solo track suppress tracks that own media', () => {
		const trackStates = resolveTrackStates({
			original: { solo: false },
			vocals: { solo: true }
		});
		const mix = resolveAudibleMix({
			trackStates,
			trackMedia: {
				original: true,
				vocals: false,
				background: true
			},
			dubLanes: []
		});

		expect(mix.hasSoloTrack).toBe(false);
		expect(mix.audibleTrackIds).toEqual(['original', 'background']);
	});

	it('applies one media-aware solo policy to source tracks and dub lanes', () => {
		const trackStates = resolveTrackStates({
			original: { solo: true },
			vocals: { solo: false },
			background: { muted: true }
		});
		const mix = resolveAudibleMix({
			trackStates,
			trackMedia: {
				original: false,
				vocals: true,
				background: true
			},
			dubLanes: [
				{ lane: 0, hasMedia: false, state: { muted: false, solo: true, volume: 1 } },
				{ lane: 1, hasMedia: true, state: { muted: false, solo: true, volume: 0.8 } },
				{ lane: 2, hasMedia: true, state: { muted: false, solo: false, volume: 1 } }
			]
		});

		expect(mix.hasSoloTrack).toBe(true);
		expect(mix.audibleTrackIds).toEqual([]);
		expect(mix.audibleDubLaneIds).toEqual([1]);
	});

	it('excludes muted and zero-volume media when no solo track is active', () => {
		const trackStates = resolveTrackStates({
			original: { solo: false, volume: 0 },
			vocals: { solo: false },
			background: { solo: false, muted: true }
		});
		const mix = resolveAudibleMix({
			trackStates,
			trackMedia: {
				original: true,
				vocals: true,
				background: true
			},
			dubLanes: [
				{ lane: 0, hasMedia: true, state: { muted: false, solo: false, volume: 0 } },
				{ lane: 1, hasMedia: true, state: { muted: false, solo: false, volume: 1 } }
			]
		});

		expect(mix.hasSoloTrack).toBe(false);
		expect(mix.audibleTrackIds).toEqual(['vocals']);
		expect(mix.audibleDubLaneIds).toEqual([1]);
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

describe('subtitle gap extension', () => {
	it('extends every positive gap to the following cue and leaves the final cue unchanged', () => {
		const cues = extendSubtitleCuesToFollowingStart([
			{ cue_id: 'cue_1', start_ms: 0, end_ms: 1000 },
			{ cue_id: 'cue_2', start_ms: 1400, end_ms: 2200 },
			{ cue_id: 'cue_3', start_ms: 3000, end_ms: 3600 }
		]);
		expect(cues.map((cue) => cue.end_ms)).toEqual([1400, 3000, 3600]);
	});

	it('does not change overlaps or mutate the source array', () => {
		const source = [
			{ cue_id: 'cue_1', start_ms: 0, end_ms: 1100 },
			{ cue_id: 'cue_2', start_ms: 1000, end_ms: 1800 }
		];
		const cues = extendSubtitleCuesToFollowingStart(source);
		expect(cues[0].end_ms).toBe(1100);
		expect(source[0].end_ms).toBe(1100);
	});
});
