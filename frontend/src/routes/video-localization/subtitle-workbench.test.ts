import { describe, expect, it } from 'vitest';
import { formatTimecode, snapToFrame, waveformBarCount, waveformBars } from './subtitle-workbench';

describe('subtitle workbench timing helpers', () => {
	it('formats positions as frame timecode', () => {
		expect(formatTimecode(3_726_500, 25)).toBe('01:02:06:13');
		expect(formatTimecode(null, 25)).toBe('--:--:--:--');
	});

	it('snaps dragged values to the nearest frame and keeps them bounded by the caller', () => {
		expect(snapToFrame(41, 25)).toBe(40);
		expect(snapToFrame(61, 25)).toBe(80);
	});

	it('compresses arbitrary peaks into a stable number of visible bars', () => {
		expect(waveformBars([0, 0.2, 0.8, 0.4], 2)).toHaveLength(2);
		expect(waveformBars([0, 0.2, 0.8, 0.4], 2)).toEqual([0.25, 1]);
	});

	it('scales waveform detail with width and bounds the rendering budget', () => {
		expect(waveformBarCount(216)).toBe(72);
		expect(waveformBarCount(810)).toBe(270);
		expect(waveformBarCount(4000)).toBe(320);
		for (const width of [0, -1, NaN, Infinity]) expect(waveformBarCount(width)).toBe(1);
	});

	it('does not fabricate samples or lose peaks during responsive resizing', () => {
		expect(waveformBars([], 270)).toEqual([]);
		expect(waveformBars([0.2, 0.8], 270)).toEqual([0.25, 1]);
		const peaks = Array.from({ length: 320 }, (_, i) => i === 319 ? 0.97 : 0.1);
		for (const width of [60, 216, 810, 4000]) {
			const bars = waveformBars(peaks, waveformBarCount(width));
			expect(bars).toHaveLength(waveformBarCount(width));
			expect(Math.max(...bars)).toBe(1);
		}
	});

	it('shows quiet audio detail without changing samples or inventing sound in silence', () => {
		const peaks = [0, 0.003, 0.03, 0.006];
		expect(waveformBars(peaks, 4)).toEqual([0, 0.1, 1, 0.2]);
		expect(peaks).toEqual([0, 0.003, 0.03, 0.006]);
		expect(waveformBars([0, 0, 0], 3)).toEqual([0, 0, 0]);
		expect(waveformBars([NaN, Infinity, -0.1], 3)).toEqual([0, 0, 1]);
	});
});
