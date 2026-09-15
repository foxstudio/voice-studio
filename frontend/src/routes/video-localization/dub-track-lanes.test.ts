import { describe, expect, it } from 'vitest';
import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import {
	buildDubTrackLaneLayout,
	canPlaceDubClipGroupAcrossLanes,
	canPlaceDubClipGroupInLane,
	canPlaceDubClipInLane,
	ensureDubTrackLaneMetadata,
	getDubTrackClipLane,
	getDubTrackLaneCount,
	placeDubClipGroupAtPrimaryLane,
	resolveDubClipLane,
	resolveDubClipGroupLane,
	resolveDubClipGroupLanePlacement,
	resolveDubHistoryDropLane,
	resolveDubSubtitleGenerationSource,
	swapDubTrackLanes,
	visibleDubTrackLanes
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
	it('uses every unmuted lane with audio for subtitle alignment and ignores solo', () => {
		const layout = buildDubTrackLaneLayout([
			clip('lane-0', 0, 1000, { dub_lane: 0, audio_path: '/tmp/0.wav' }),
			clip('lane-1', 0, 1000, { dub_lane: 1, audio_path: '/tmp/1.wav' }),
			clip('lane-2-no-audio', 0, 1000, { dub_lane: 2 })
		]);
		const source = resolveDubSubtitleGenerationSource(layout, {
			'0': { muted: false, solo: false, volume: 1 },
			'1': { muted: false, solo: true, volume: 1 },
			'2': { muted: false, solo: false, volume: 1 }
		});

		expect(source).toEqual({
			hasContent: true,
			unmutedLaneIds: [0, 1],
			allContentLanesMuted: false,
			unavailableReason: ''
		});
	});

	it('distinguishes an empty dub track from all content lanes being muted', () => {
		const layout = buildDubTrackLaneLayout([
			clip('lane-0', 0, 1000, { dub_lane: 0, audio_path: '/tmp/0.wav' }),
			clip('lane-1', 0, 1000, { dub_lane: 1, audio_path: '/tmp/1.wav' })
		]);
		expect(resolveDubSubtitleGenerationSource(layout, {
			'0': { muted: true, solo: false, volume: 1 },
			'1': { muted: true, solo: true, volume: 1 }
		})).toMatchObject({
			hasContent: true,
			unmutedLaneIds: [],
			allContentLanesMuted: true
		});
		expect(resolveDubSubtitleGenerationSource(buildDubTrackLaneLayout([]), {})).toEqual({
			hasContent: false,
			unmutedLaneIds: [],
			allContentLanesMuted: false,
			unavailableReason: '合成配音轨有可用音频后，才能重新识别字幕'
		});
	});

	it('can expose a temporary lane while a drag preview is active', () => {
		const layout = buildDubTrackLaneLayout([clip('only', 0, 1000, { dub_lane: 0 })]);
		const lanes = visibleDubTrackLanes(layout, ['0', '1', '2']);

		expect(lanes).toHaveLength(3);
		expect(lanes[0].map((item) => item.clip_id)).toEqual(['only']);
		expect(lanes[1]).toEqual([]);
		expect(lanes[2]).toEqual([]);
	});

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

	it('swaps two occupied dub lanes without touching other audio tracks', () => {
		const clips = [
			clip('first', 0, 1000, { dub_lane: 0 }),
			clip('second', 0, 1000, { dub_lane: 1 }),
			clip('music', 0, 1000, { track_id: 'background' })
		];

		const swapped = swapDubTrackLanes(clips, 0, 1);

		expect(swapped.map((item) => item.dub_lane)).toEqual([1, 0, undefined]);
		expect(swapped[2]).toBe(clips[2]);
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

	it('never compacts valid user-assigned lanes when filling legacy metadata', () => {
		const assigned = clip('assigned', 0, 1000, { dub_lane: 3 });
		const legacy = clip('legacy', 1200, 2000);
		const next = ensureDubTrackLaneMetadata([assigned, legacy]);

		expect(next[0]).toBe(assigned);
		expect(next[0].dub_lane).toBe(3);
		expect(next[1].dub_lane).toBe(0);
	});

	it('renders overlapping explicitly assigned clips in their persisted lane', () => {
		const layout = buildDubTrackLaneLayout([
			clip('a', 0, 1000, { lane: 0 }),
			clip('b', 500, 1500, { lane: 0 }),
			clip('c', 1000, 2000, { lane_index: 1 })
		]);

		expect(lanesFor(layout)).toEqual([
			['a', 'b'],
			['c']
		]);
	});

	it('previews a history drop on the requested lane when it is free', () => {
		expect(resolveDubHistoryDropLane([clip('existing', 0, 1000)], 1200, 2200, 0)).toBe(0);
	});

	it('previews a history drop on a new lane when the requested lane overlaps', () => {
		expect(resolveDubHistoryDropLane([clip('existing', 0, 2000)], 500, 1500, 0)).toBe(1);
	});

	it('uses another existing lane before creating an unnecessary lane', () => {
		expect(resolveDubHistoryDropLane([
			clip('lane-zero', 0, 2000),
			clip('lane-one', 0, 400, { dub_lane: 1 })
		], 500, 1500, 0)).toBe(1);
	});

	it('moves only the edited clip to the first free lane when its lane collides', () => {
		expect(resolveDubClipLane([
			clip('moving', 0, 400, { dub_lane: 0 }),
			clip('lane-zero', 1000, 2000, { dub_lane: 0 }),
			clip('lane-one', 0, 600, { dub_lane: 1 })
		], 1200, 1800, 0, 'moving')).toBe(1);
	});

	it('skips locked lanes when resolving a history drop', () => {
		expect(resolveDubHistoryDropLane([
			clip('lane-zero', 0, 2000, { dub_lane: 0 })
		], 500, 1500, 0, [1])).toBe(2);
	});

	it('allows a clip to move vertically into a free lane while excluding itself', () => {
		expect(canPlaceDubClipInLane([
			clip('moving', 0, 1000, { dub_lane: 0 }),
			clip('lane-one-later', 1500, 2500, { dub_lane: 1 })
		], 200, 1200, 1, 'moving')).toBe(true);
	});

	it('blocks a vertical move that would overlap the target lane', () => {
		expect(canPlaceDubClipInLane([
			clip('moving', 0, 1000, { dub_lane: 0 }),
			clip('lane-one', 800, 1800, { dub_lane: 1 })
		], 200, 1200, 1, 'moving')).toBe(false);
	});

	it('allows adjacent clips on a vertical move target', () => {
		expect(canPlaceDubClipInLane([
			clip('moving', 0, 1000, { dub_lane: 0 }),
			clip('lane-one', 1200, 1800, { dub_lane: 1 })
		], 200, 1200, 1, 'moving')).toBe(true);
	});

	it('reuses an existing free lane for a moving group before offering a new lane', () => {
		const clips = [
			clip('moving-a', 0, 500, { dub_lane: 0 }),
			clip('moving-b', 600, 1000, { dub_lane: 0 }),
			clip('lane-zero-blocker', 1200, 2200, { dub_lane: 0 }),
			clip('lane-one-anchor', 0, 300, { dub_lane: 1 })
		];
		const ranges = [
			{ clipId: 'moving-a', startMs: 1300, endMs: 1700 },
			{ clipId: 'moving-b', startMs: 1750, endMs: 2150 }
		];

		expect(canPlaceDubClipGroupInLane(clips, ranges, 0)).toBe(false);
		expect(canPlaceDubClipGroupInLane(clips, ranges, 1)).toBe(true);
		expect(resolveDubClipGroupLane(clips, ranges, 2)).toBe(1);
	});

	it('offers a new lane only when every existing lane overlaps the moving group', () => {
		const clips = [
			clip('moving-a', 0, 500, { dub_lane: 0 }),
			clip('moving-b', 600, 1000, { dub_lane: 0 }),
			clip('lane-zero-blocker', 1200, 2200, { dub_lane: 0 }),
			clip('lane-one-blocker', 1500, 2500, { dub_lane: 1 })
		];
		const ranges = [
			{ clipId: 'moving-a', startMs: 1300, endMs: 1700 },
			{ clipId: 'moving-b', startMs: 1750, endMs: 2150 }
		];

		expect(resolveDubClipGroupLane(clips, ranges, 2)).toBe(2);
	});

	it('does not flatten overlapping selected clips into one target lane', () => {
		const ranges = [
			{ clipId: 'moving-a', startMs: 1000, endMs: 2000 },
			{ clipId: 'moving-b', startMs: 1500, endMs: 2500 }
		];

		expect(canPlaceDubClipGroupInLane([], ranges, 0)).toBe(false);
	});

	it('preserves relative lanes when overlapping selected clips move together', () => {
		const clips = [
			clip('moving-a', 0, 1000, { dub_lane: 0 }),
			clip('moving-b', 400, 1400, { dub_lane: 1 }),
			clip('lane-zero-blocker', 2000, 3000, { dub_lane: 0 })
		];
		const placement = placeDubClipGroupAtPrimaryLane(clips, [
			{ clipId: 'moving-a', startMs: 2200, endMs: 3200, lane: 0 },
			{ clipId: 'moving-b', startMs: 2600, endMs: 3600, lane: 1 }
		], 'moving-a', 1);

		expect(placement).toMatchObject({
			primaryLane: 1,
			laneByClipId: { 'moving-a': 1, 'moving-b': 2 },
			targetLanes: [1, 2]
		});
	});

	it('adds only the lanes required by a preserved multi-lane group', () => {
		const clips = [
			clip('moving-a', 0, 1000, { dub_lane: 0 }),
			clip('moving-b', 400, 1400, { dub_lane: 1 }),
			clip('lane-zero-blocker', 2000, 4000, { dub_lane: 0 }),
			clip('lane-one-blocker', 2000, 4000, { dub_lane: 1 })
		];
		const placement = resolveDubClipGroupLanePlacement(clips, [
			{ clipId: 'moving-a', startMs: 2200, endMs: 3200, lane: 0 },
			{ clipId: 'moving-b', startMs: 2600, endMs: 3600, lane: 1 }
		], 'moving-a', 2);

		expect(placement).toMatchObject({
			primaryLane: 2,
			laneByClipId: { 'moving-a': 2, 'moving-b': 3 },
			targetLanes: [2, 3]
		});
	});

	it('accepts a multi-lane commit while excluding every moving clip from obstacles', () => {
		const clips = [
			clip('moving-a', 0, 1000, { dub_lane: 0 }),
			clip('moving-b', 400, 1400, { dub_lane: 1 }),
			clip('stationary', 0, 500, { dub_lane: 2 })
		];

		expect(canPlaceDubClipGroupAcrossLanes(clips, [
			{ clipId: 'moving-a', startMs: 500, endMs: 1500, lane: 1 },
			{ clipId: 'moving-b', startMs: 900, endMs: 1900, lane: 2 }
		])).toBe(true);
	});

	it('rejects a multi-lane commit when any target lane is locked or conflicts', () => {
		const clips = [
			clip('moving-a', 0, 1000, { dub_lane: 0 }),
			clip('moving-b', 400, 1400, { dub_lane: 1 }),
			clip('stationary', 1200, 2200, { dub_lane: 2 })
		];
		const ranges = [
			{ clipId: 'moving-a', startMs: 500, endMs: 1500, lane: 1 },
			{ clipId: 'moving-b', startMs: 900, endMs: 1900, lane: 2 }
		];

		expect(canPlaceDubClipGroupAcrossLanes(clips, ranges)).toBe(false);
		expect(canPlaceDubClipGroupAcrossLanes(
			clips.filter((item) => item.clip_id !== 'stationary'),
			ranges,
			[2]
		)).toBe(false);
	});
});
