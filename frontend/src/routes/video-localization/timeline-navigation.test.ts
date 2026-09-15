import { describe, expect, it } from 'vitest';
import { timelineBoundaryTarget, timelineEditPoints, type TimelineNavigationSources } from './timeline-navigation';

function sources(): TimelineNavigationSources {
	return {
		durationMs: 10_000,
		frameRate: 25,
		asrCues: [{ start_ms: 1_000, end_ms: 2_000 }],
		localizedSubtitles: [{ start_ms: 1_500, end_ms: 3_000 }],
		audioClips: [
			{ start_ms: 2_000, end_ms: 4_000 },
			{ start_ms: 6_000, end_ms: 8_000 },
			{ start_ms: -10, end_ms: 12_000 },
			{ start_ms: Number.NaN, end_ms: null }
		]
	};
}

describe('global timeline edit-point navigation', () => {
	it('collects and deduplicates in/out points from every track', () => {
		expect(timelineEditPoints(sources())).toEqual([1_000, 1_500, 2_000, 3_000, 4_000, 6_000, 8_000]);
	});

	it('moves to the adjacent global point without stopping on the current point', () => {
		expect(timelineBoundaryTarget(sources(), 2_000, 'previous')).toBe(1_500);
		expect(timelineBoundaryTarget(sources(), 2_000, 'next')).toBe(3_000);
		expect(timelineBoundaryTarget(sources(), 2_010, 'previous')).toBe(2_000);
	});

	it('falls back to the video head and last playable frame', () => {
		expect(timelineBoundaryTarget(sources(), 500, 'previous')).toBe(0);
		expect(timelineBoundaryTarget(sources(), 9_500, 'next')).toBe(9_960);
		expect(timelineBoundaryTarget({ ...sources(), audioClips: [{ start_ms: 9_900, end_ms: 10_000 }] }, 9_900, 'next')).toBe(9_960);
		expect(timelineBoundaryTarget({ ...sources(), audioClips: [{ start_ms: 9_900, end_ms: 10_000 }] }, 10_000, 'next')).toBe(9_960);
	});
});
