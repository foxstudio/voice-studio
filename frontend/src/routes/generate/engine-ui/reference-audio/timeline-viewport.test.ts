import { describe, expect, it } from 'vitest';
import { buildReferenceTimelineTicks, maximumReferenceTimelineZoom } from './timeline-viewport';

describe('reference timeline viewport', () => {
	it('keeps short audio zoomed to a useful time span', () => {
		expect(maximumReferenceTimelineZoom(0)).toBe(1);
		expect(maximumReferenceTimelineZoom(2.2)).toBeCloseTo(4.4);
		expect(maximumReferenceTimelineZoom(3600)).toBe(1200);
	});
	it.each([390, 900])('keeps readable labels visible at maximum short-clip zoom (%i px)', (width) => {
		const duration = 2.2;
		const zoom = maximumReferenceTimelineZoom(duration);
		const ticks = buildReferenceTimelineTicks(duration, zoom, width);
		for (const start of [0, 0.5, 1, 1.5]) {
			expect(ticks.filter(t => t.label && t.time >= start && t.time <= start + duration / zoom).length).toBeGreaterThanOrEqual(2);
		}
		expect(ticks.at(-1)?.percent).toBeCloseTo(100);
		const labels = ticks.filter(t => t.label).map(t => t.label);
		expect(new Set(labels).size).toBe(labels.length);
	});
});
