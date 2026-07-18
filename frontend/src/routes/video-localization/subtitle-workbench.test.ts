import { describe, expect, it } from 'vitest';
import { formatTimecode, snapToFrame, waveformBars } from './subtitle-workbench';

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
		expect(waveformBars([0, 0.2, 0.8, 0.4], 2)[1]).toBe(0.8);
	});
});
