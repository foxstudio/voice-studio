import { describe, expect, it } from 'vitest';
import { timelinePointerIntent } from './timeline-interaction';

describe('timeline pointer intent', () => {
	it('starts range creation from an ordinary primary press on a track', () => {
		expect(timelinePointerIntent({ button: 0, overTimeline: true, overTrack: true, interactive: false })).toBe('range-create');
	});

	it('keeps ruler presses in seek mode', () => {
		expect(timelinePointerIntent({ button: 0, overTimeline: true, overTrack: false, interactive: false })).toBe('seek');
	});

	it('preserves middle-button panning and interactive controls', () => {
		expect(timelinePointerIntent({ button: 1, overTimeline: true, overTrack: true, interactive: false })).toBe('pan');
		expect(timelinePointerIntent({ button: 0, overTimeline: true, overTrack: true, interactive: true })).toBe('ignore');
	});
});
