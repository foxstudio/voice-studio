import { describe, expect, it } from 'vitest';
import { isRepeatedTimelinePress, timelinePointerIntent } from './timeline-interaction';

describe('timeline pointer intent', () => {
	it('uses the second mouse press for range creation', () => {
		expect(timelinePointerIntent({ button: 0, clickCount: 2, overTimeline: true, overTrack: true, interactive: false })).toBe('range-create');
	});

	it('keeps ordinary track clicks and drags in seek mode', () => {
		expect(timelinePointerIntent({ button: 0, clickCount: 1, overTimeline: true, overTrack: true, interactive: false })).toBe('seek');
	});

	it('preserves middle-button panning and interactive controls', () => {
		expect(timelinePointerIntent({ button: 1, clickCount: 1, overTimeline: true, overTrack: true, interactive: false })).toBe('pan');
		expect(timelinePointerIntent({ button: 0, clickCount: 2, overTimeline: true, overTrack: true, interactive: true })).toBe('ignore');
	});

	it('recognizes a second nearby press without depending on browser clickCount', () => {
		expect(isRepeatedTimelinePress(
			{ time: 1000, x: 200, y: 300 },
			{ time: 1280, x: 205, y: 304 }
		)).toBe(true);
		expect(isRepeatedTimelinePress(
			{ time: 1000, x: 200, y: 300 },
			{ time: 1450, x: 205, y: 304 }
		)).toBe(true);
		expect(isRepeatedTimelinePress(
			{ time: 1000, x: 200, y: 300 },
			{ time: 1520, x: 205, y: 304 }
		)).toBe(false);
	});
});
