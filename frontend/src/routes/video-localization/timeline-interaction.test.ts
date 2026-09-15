import { describe, expect, it } from 'vitest';
import {
	isRepeatedPrimaryPress,
	resolveTimelineSubtitleHit,
	resolveTimelineSubtitleMerge,
	timelinePointerIntent
} from './timeline-interaction';

describe('timeline pointer intent', () => {
	it('starts clip marquee selection from an ordinary primary press on a track', () => {
		expect(timelinePointerIntent({ button: 0, detail: 1, overTimeline: true, overTrack: true, interactive: false })).toBe('marquee-select');
	});

	it('reserves the held second press for time-range creation', () => {
		expect(timelinePointerIntent({ button: 0, detail: 2, overTimeline: true, overTrack: true, interactive: false })).toBe('range-create');
	});

	it('keeps ruler presses in seek mode', () => {
		expect(timelinePointerIntent({ button: 0, overTimeline: true, overTrack: false, interactive: false })).toBe('seek');
	});

	it('preserves middle-button panning and interactive controls', () => {
		expect(timelinePointerIntent({ button: 1, overTimeline: true, overTrack: true, interactive: false })).toBe('pan');
		expect(timelinePointerIntent({ button: 0, overTimeline: true, overTrack: true, interactive: true })).toBe('ignore');
	});

	it('recognizes a nearby second press even when PointerEvent.detail is unavailable', () => {
		expect(isRepeatedPrimaryPress({ detail: 0, elapsedMs: 240, distancePx: 4 })).toBe(true);
		expect(isRepeatedPrimaryPress({ detail: 0, elapsedMs: 420, distancePx: 4 })).toBe(false);
		expect(isRepeatedPrimaryPress({ detail: 0, elapsedMs: 180, distancePx: 12 })).toBe(false);
	});
});

describe('timeline subtitle merge selection', () => {
	it('sorts ASR subtitles by time and returns merged text and outer bounds', () => {
		const result = resolveTimelineSubtitleMerge(
			[
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_02' },
				{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' }
			],
			[
				{ cue_id: 'cue_02', start_ms: 2400, end_ms: 3900, en_subtitle_text: 'Second line' },
				{ cue_id: 'cue_01', start_ms: 800, end_ms: 2100, en_subtitle_text: 'First line' }
			] as never,
			[]
		);

		expect(result).toEqual({
			track: 'asr',
			itemIds: ['cue_01', 'cue_02'],
			startMs: 800,
			endMs: 3900,
			text: 'First line\nSecond line'
		});
	});

	it('supports two or more localized subtitles on the same track', () => {
		const result = resolveTimelineSubtitleMerge(
			[
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_03' },
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_01' },
				{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_02' }
			],
			[],
			[
				{ subtitle_id: 'localized_01', start_ms: 100, end_ms: 600, text: '第一句' },
				{ subtitle_id: 'localized_02', start_ms: 700, end_ms: 1200, text: '第二句' },
				{ subtitle_id: 'localized_03', start_ms: 1400, end_ms: 2000, text: '第三句' }
			] as never
		);

		expect(result).toMatchObject({
			track: 'localized',
			itemIds: ['localized_01', 'localized_02', 'localized_03'],
			startMs: 100,
			endMs: 2000,
			text: '第一句\n第二句\n第三句'
		});
	});

	it('rejects single, mixed-track, audio, or missing subtitle selections', () => {
		const cue = [{ cue_id: 'cue_01', start_ms: 0, end_ms: 1000, en_subtitle_text: 'One' }] as never;
		expect(resolveTimelineSubtitleMerge([{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' }], cue, [])).toBeNull();
		expect(resolveTimelineSubtitleMerge([
			{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' },
			{ kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'localized_01' }
		], cue, [])).toBeNull();
		expect(resolveTimelineSubtitleMerge([
			{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' },
			{ kind: 'audio', trackId: 'vocals', itemId: 'audio_01' }
		], cue, [])).toBeNull();
		expect(resolveTimelineSubtitleMerge([
			{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_01' },
			{ kind: 'subtitle', trackId: 'subtitles', itemId: 'cue_missing' }
		], cue, [])).toBeNull();
	});
});

describe('timeline subtitle semantic hit testing', () => {
	const sequential = [
		{ itemId: 'cue_01', startMs: 15_600, endMs: 17_600 },
		{ itemId: 'cue_02', startMs: 17_600, endMs: 19_620 },
		{ itemId: 'cue_03', startMs: 19_620, endMs: 21_400 }
	];

	it('recovers the cue under the timeline time when a neighboring fractional-pixel button wins DOM hit testing', () => {
		expect(resolveTimelineSubtitleHit(16_500, 'cue_02', sequential)).toBe('cue_01');
		expect(resolveTimelineSubtitleHit(18_500, 'cue_03', sequential)).toBe('cue_02');
	});

	it('keeps the DOM target when its real interval contains the pointer, including semantic overlaps', () => {
		const overlapping = [
			{ itemId: 'wide', startMs: 1_000, endMs: 4_000 },
			{ itemId: 'specific', startMs: 2_000, endMs: 2_500 }
		];
		expect(resolveTimelineSubtitleHit(2_250, 'wide', overlapping)).toBe('wide');
		expect(resolveTimelineSubtitleHit(2_250, 'specific', overlapping)).toBe('specific');
	});

	it('uses half-open boundaries and retains the DOM target when no semantic interval matches', () => {
		expect(resolveTimelineSubtitleHit(17_600, 'cue_01', sequential)).toBe('cue_02');
		expect(resolveTimelineSubtitleHit(25_000, 'cue_03', sequential)).toBe('cue_03');
		expect(resolveTimelineSubtitleHit(Number.NaN, 'cue_03', sequential)).toBe('cue_03');
	});
});
