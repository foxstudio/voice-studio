import { describe, expect, it } from 'vitest';
import { timelineWheelIntent, trackpadPinchZoomScale } from './timeline-wheel';

const base = {
	ctrlKey: false,
	metaKey: false,
	shiftKey: false,
	deltaX: 0,
	deltaY: 0,
	deltaMode: 0
};

describe('timeline wheel intent', () => {
	it('routes Mac trackpad pinch and Command + two-finger scrolling to continuous zoom', () => {
		expect(timelineWheelIntent({ ...base, ctrlKey: true, deltaY: -3.2 })).toBe('trackpad-zoom');
		expect(timelineWheelIntent({ ...base, metaKey: true, deltaY: 2.4 })).toBe('trackpad-zoom');
	});

	it('pans for horizontal two-finger movement', () => {
		expect(timelineWheelIntent({ ...base, deltaX: 18, deltaY: 3 })).toBe('pan');
		expect(timelineWheelIntent({ ...base, shiftKey: true, deltaY: 12 })).toBe('pan');
	});

	it('keeps continuous vertical tablet movement as native scrolling', () => {
		expect(timelineWheelIntent({ ...base, deltaY: 3.25 })).toBe('native-scroll');
		expect(timelineWheelIntent({ ...base, deltaY: 27 })).toBe('native-scroll');
		expect(timelineWheelIntent({ ...base, deltaY: 100 })).toBe('native-scroll');
	});

	it('keeps unmodified physical mouse-wheel events on their discrete zoom path', () => {
		expect(timelineWheelIntent({ ...base, deltaMode: 1, deltaY: 3 })).toBe('wheel-zoom');
		expect(timelineWheelIntent({ ...base, deltaY: 4, wheelDeltaY: -120 })).toBe('wheel-zoom');
	});

	it('keeps modifier-wheel input on the gentle zoom path even with discrete-looking deltas', () => {
		expect(timelineWheelIntent({ ...base, ctrlKey: true, deltaMode: 1, deltaY: 3 })).toBe('trackpad-zoom');
		expect(timelineWheelIntent({ ...base, ctrlKey: true, deltaY: 4, wheelDeltaY: -120 })).toBe('trackpad-zoom');
	});

	it('keeps continuous trackpad zoom deliberately slow', () => {
		expect(trackpadPinchZoomScale(-3)).toBeCloseTo(1.0305, 4);
		expect(trackpadPinchZoomScale(3)).toBeCloseTo(0.9704, 4);
		expect(trackpadPinchZoomScale(-24)).toBeCloseTo(1.2712, 4);
		expect(trackpadPinchZoomScale(-240)).toBeCloseTo(trackpadPinchZoomScale(-24), 8);
		expect(trackpadPinchZoomScale(Number.NaN)).toBe(1);
	});
});
