import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	buildDubTrackLaneLayout,
	getDubTrackClipLane,
	getDubTrackLaneCount
} from './dub-track-lanes';

function clip(
	clipId: string,
	startMs: number | null,
	endMs: number | null,
	overrides: Partial<VideoLocalizationTimelineClip> = {}
): VideoLocalizationTimelineClip {
	return {
		clip_id: clipId,
		track_id: 'dub',
		start_ms: startMs,
		end_ms: endMs,
		...overrides
	};
}

function lanesFor(layout: ReturnType<typeof buildDubTrackLaneLayout>) {
	return layout.lanes.map((lane) => lane.map((item) => item.clip_id));
}

describe('dub track lane layout', () => {
	it('puts non-overlapping clips in one lane and ignores non-dub or invalid ranges', () => {
		const layout = buildDubTrackLaneLayout([
			clip('a', 0, 1000),
			clip('other', 0, 500, { track_id: 'vocals' }),
			clip('missing', null, 500),
			clip('empty', 500, 500),
			clip('b', 1500, 2200)
		]);

		expect(lanesFor(layout)).toEqual([['a', 'b']]);
		expect(getDubTrackLaneCount(layout)).toBe(1);
		expect(getDubTrackClipLane(layout, 'other')).toBeUndefined();
	});

	it('reuses the earliest lane through a chain of overlaps', () => {
		const layout = buildDubTrackLaneLayout([
			clip('a', 0, 1000),
			clip('b', 500, 1500),
			clip('c', 1000, 2000)
		]);

		expect(lanesFor(layout)).toEqual([
			['a', 'c'],
			['b']
		]);
	});

	it('creates a lane for every fully nested overlap', () => {
		const layout = buildDubTrackLaneLayout([
			clip('outer', 0, 3000),
			clip('middle', 500, 2500),
			clip('inner', 1000, 2000)
		]);

		expect(lanesFor(layout)).toEqual([['outer'], ['middle'], ['inner']]);
		expect(layout.laneCount).toBe(3);
	});

	it('treats adjacent half-open intervals as non-overlapping', () => {
		const first = clip('first', 0, 1000);
		const second = clip('second', 1000, 2000);
		const layout = buildDubTrackLaneLayout([first, second]);

		expect(lanesFor(layout)).toEqual([['first', 'second']]);
		expect(getDubTrackClipLane(layout, second)).toBe(0);
	});

	it('sorts by start time while preserving input order for equal starts', () => {
		const layout = buildDubTrackLaneLayout([
			clip('late', 2000, 3000),
			clip('same-first', 0, 1000),
			clip('same-second', 0, 500),
			clip('middle', 1000, 2000)
		]);

		expect(layout.assignments.map(({ clip: item, lane }) => [item.clip_id, lane])).toEqual([
			['same-first', 0],
			['same-second', 1],
			['middle', 0],
			['late', 0]
		]);
	});

	it('reuses a replaced clip lane from the previous layout when it remains valid', () => {
		const initial = buildDubTrackLaneLayout([
			clip('base', 0, 3000),
			clip('replace-me', 500, 1500),
			clip('later', 1600, 2200)
		]);
		const replaced = clip('replace-me', 600, 1400, { audio_path: '/tmp/new.wav' });
		const next = buildDubTrackLaneLayout(
			[clip('later', 1600, 2200), replaced, clip('base', 0, 3000)],
			initial
		);

		expect(getDubTrackClipLane(initial, 'replace-me')).toBe(1);
		expect(getDubTrackClipLane(next, replaced)).toBe(1);
		expect(lanesFor(next)).toEqual([['base'], ['replace-me', 'later']]);
	});

	it('keeps available lane metadata instead of unnecessarily moving a clip', () => {
		const layout = buildDubTrackLaneLayout([
			clip('lane-zero', 0, 1000),
			clip('lane-one', 0, 500),
			clip('hinted', 1000, 2000, { dub_lane: 1 })
		]);

		expect(getDubTrackClipLane(layout, 'hinted')).toBe(1);
		expect(lanesFor(layout)).toEqual([
			['lane-zero'],
			['lane-one', 'hinted']
		]);
	});

	it('drops a conflicting lane hint and uses the earliest available lane', () => {
		const layout = buildDubTrackLaneLayout([
			clip('a', 0, 1000, { lane: 0 }),
			clip('b', 500, 1500, { lane: 0 }),
			clip('c', 1000, 2000, { lane_index: 1 })
		]);

		expect(lanesFor(layout)).toEqual([
			['a', 'c'],
			['b']
		]);
	});
});
