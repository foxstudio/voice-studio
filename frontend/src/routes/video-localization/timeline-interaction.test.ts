import { describe, expect, it } from 'vitest';
import { isRepeatedPrimaryPress, timelinePointerIntent } from './timeline-interaction';

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
