import { describe, expect, it } from 'vitest';
import { resizedTimelineScrollLeft } from './timeline-viewport';

describe('timeline viewport resize anchoring', () => {
	it('keeps the visible time when an inspector adds a 15px page scrollbar at high zoom', () => {
		const left = resizedTimelineScrollLeft(40857, 723, 708, 89562);
		expect(left).toBeCloseTo(40009.3444, 3);
		expect(left / 708).toBeCloseTo(40857 / 723, 10);
	});

	it('returns to the same time after shrinking and expanding', () => {
		const left = resizedTimelineScrollLeft(40857, 723, 708, 89562);
		expect(resizedTimelineScrollLeft(left, 708, 723, 91460)).toBeCloseTo(40857, 8);
	});

	it('clamps to the available scroll range, including an unzoomed timeline', () => {
		expect(resizedTimelineScrollLeft(990, 100, 200, 2000)).toBe(1800);
		expect(resizedTimelineScrollLeft(100, 723, 708, 708)).toBe(0);
	});

	it('does not invent an anchor before first measurement', () => {
		expect(resizedTimelineScrollLeft(80, 0, 708, 1416)).toBe(80);
	});
});
