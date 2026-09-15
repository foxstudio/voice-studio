import { describe, expect, it } from 'vitest';
import { resolveTimelineGroupMove, type TimelineGroupMoveItem } from './timeline-group-move';

function item(
	itemId: string,
	startMs: number,
	endMs: number,
	kind: TimelineGroupMoveItem['kind'] = 'subtitle',
	trackId = kind === 'subtitle' ? 'localizedSubtitles' : 'dub'
): TimelineGroupMoveItem {
	return { kind, trackId, itemId, startMs, endMs };
}

describe('timeline group move', () => {
	it('moves mixed subtitle and audio items by one shared delta without changing duration', () => {
		const selectedItems = [
			item('subtitle-1', 1000, 1800),
			item('audio-1', 2400, 4000, 'audio')
		];
		const result = resolveTimelineGroupMove({
			selectedItems,
			primary: selectedItems[1],
			requestedDeltaMs: 750,
			timelineDurationMs: 10_000
		});

		expect(result).toMatchObject({
			requestedDeltaMs: 750,
			appliedDeltaMs: 750,
			minDeltaMs: -1000,
			maxDeltaMs: 6000,
			constrained: false
		});
		expect(result.items).toEqual([
			{
				kind: 'subtitle', trackId: 'localizedSubtitles', itemId: 'subtitle-1',
				originalStartMs: 1000, originalEndMs: 1800, startMs: 1750, endMs: 2550, durationMs: 800
			},
			{
				kind: 'audio', trackId: 'dub', itemId: 'audio-1',
				originalStartMs: 2400, originalEndMs: 4000, startMs: 3150, endMs: 4750, durationMs: 1600
			}
		]);
		expect(result.primary).toEqual(result.items[1]);
		expect(selectedItems).toEqual([
			item('subtitle-1', 1000, 1800),
			item('audio-1', 2400, 4000, 'audio')
		]);
	});

	it('limits a left move using the earliest selected item', () => {
		const selectedItems = [item('first', 200, 900), item('second', 1400, 2200)];
		const result = resolveTimelineGroupMove({
			selectedItems,
			primary: selectedItems[1],
			requestedDeltaMs: -500,
			timelineDurationMs: 5000
		});

		expect(result.appliedDeltaMs).toBe(-200);
		expect(result.constrained).toBe(true);
		expect(result.items.map(({ startMs, endMs }) => [startMs, endMs])).toEqual([
			[0, 700],
			[1200, 2000]
		]);
	});

	it('limits a right move using the latest selected item', () => {
		const selectedItems = [item('first', 500, 1000), item('last', 4200, 4800, 'audio')];
		const result = resolveTimelineGroupMove({
			selectedItems,
			primary: selectedItems[0],
			requestedDeltaMs: 900,
			timelineDurationMs: 5000
		});

		expect(result.appliedDeltaMs).toBe(200);
		expect(result.items.map(({ startMs, endMs }) => [startMs, endMs])).toEqual([
			[700, 1200],
			[4400, 5000]
		]);
	});

	it('returns zero movement when the selected group already spans the timeline', () => {
		const selectedItems = [item('whole', 0, 5000, 'audio')];
		const result = resolveTimelineGroupMove({
			selectedItems,
			primary: selectedItems[0],
			requestedDeltaMs: 1000,
			timelineDurationMs: 5000
		});

		expect(result).toMatchObject({ appliedDeltaMs: 0, minDeltaMs: 0, maxDeltaMs: 0, constrained: true });
		expect(result.primary).toMatchObject({ startMs: 0, endMs: 5000, durationMs: 5000 });
	});

	it('respects collision bounds supplied by the owning track', () => {
		const selectedItems = [item('first', 1000, 1600), item('second', 1700, 2300)];
		const result = resolveTimelineGroupMove({
			selectedItems,
			primary: selectedItems[0],
			requestedDeltaMs: 900,
			timelineDurationMs: 5000,
			minimumDeltaMs: -400,
			maximumDeltaMs: 300
		});

		expect(result.appliedDeltaMs).toBe(300);
		expect(result.maxDeltaMs).toBe(300);
	});

	it('rejects invalid input before producing an out-of-bounds preview or commit payload', () => {
		const valid = item('valid', 100, 500);
		expect(() => resolveTimelineGroupMove({
			selectedItems: [], primary: valid, requestedDeltaMs: 0, timelineDurationMs: 1000
		})).toThrow('selectedItems must not be empty');
		expect(() => resolveTimelineGroupMove({
			selectedItems: [item('outside', 100, 1100)], primary: item('outside', 100, 1100), requestedDeltaMs: 0, timelineDurationMs: 1000
		})).toThrow('must be a non-empty range inside the timeline');
		expect(() => resolveTimelineGroupMove({
			selectedItems: [valid], primary: item('other', 100, 500), requestedDeltaMs: 0, timelineDurationMs: 1000
		})).toThrow('primary must be included in selectedItems');
		expect(() => resolveTimelineGroupMove({
			selectedItems: [valid], primary: { ...valid, endMs: 600 }, requestedDeltaMs: 0, timelineDurationMs: 1000
		})).toThrow('primary range must match the selected item range');
		expect(() => resolveTimelineGroupMove({
			selectedItems: [valid], primary: valid, requestedDeltaMs: Number.NaN, timelineDurationMs: 1000
		})).toThrow('requestedDeltaMs must be finite');
	});
});
