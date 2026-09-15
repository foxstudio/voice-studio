import { describe, expect, it } from 'vitest';
import { waveformMeterLevelAt } from './timeline-meter';

describe('timeline audio meter', () => {
	it('interpolates between waveform bins so the meter can update every playback frame', () => {
		const bars = [0.05, 0.2, 0.9, 0.4, 0.1];

		expect(waveformMeterLevelAt(bars, 2_500, 5_000)).toBe(0.9);
		expect(waveformMeterLevelAt(bars, 4_375, 5_000)).toBeCloseTo(0.25);
	});

	it('returns silence for missing or invalid waveform data', () => {
		expect(waveformMeterLevelAt([], 1_000, 5_000)).toBe(0);
		expect(waveformMeterLevelAt([0.5], 1_000, 0)).toBe(0);
	});
});
