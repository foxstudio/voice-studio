import { describe, expect, it } from 'vitest';
import { canRetainClipWaveformDetail, resolveClipWaveformSourceWindow } from './clip-waveform-geometry';

describe('clip waveform detail retention', () => {
	const current = {
		resourceIdentity: 'audio?v=one', nextResourceIdentity: 'audio?v=one',
		resolution: 'detail' as const, hasPeaks: true,
		windowStartMs: 1000, windowEndMs: 5000, visibleStartMs: 1500, visibleEndMs: 4000
	};
	it('keeps loaded detail after trimming within its source window', () => {
		expect(canRetainClipWaveformDetail(current)).toBe(true);
		expect(canRetainClipWaveformDetail({ ...current, visibleStartMs: 1000, visibleEndMs: 5000 })).toBe(true);
	});
	it('keeps the loaded overlap while newly exposed samples are loading', () => {
		expect(canRetainClipWaveformDetail({ ...current, visibleStartMs: 999 })).toBe(true);
		expect(canRetainClipWaveformDetail({ ...current, visibleEndMs: 5001 })).toBe(true);
		expect(canRetainClipWaveformDetail({ ...current, resolution: 'preview' })).toBe(true);
	});
	it.each([
		{ visibleStartMs: 5000, visibleEndMs: 6000 }, { visibleStartMs: 0, visibleEndMs: 1000 },
		{ nextResourceIdentity: 'audio?v=two' }, { resourceIdentity: '', nextResourceIdentity: '' },
		{ resolution: 'none' as const }, { hasPeaks: false },
		{ visibleStartMs: NaN }, { windowEndMs: Infinity }, { visibleEndMs: 1500 }
	])('does not reuse uncovered or unrelated detail: %j', update => {
		expect(canRetainClipWaveformDetail({ ...current, ...update })).toBe(false);
	});
});

describe('clip waveform geometry', () => {
	it('uses the visible clip duration when the source end is not stored yet', () => {
		expect(resolveClipWaveformSourceWindow({
			audioDurationMs: 30_000,
			sourceStartMs: 2_000,
			sourceEndMs: null,
			clipStartMs: 10_000,
			clipEndMs: 14_000
		})).toEqual({ sourceStartMs: 2_000, sourceEndMs: 6_000 });
	});

	it('keeps explicit trimmed source boundaries and clamps them to decoded audio', () => {
		expect(resolveClipWaveformSourceWindow({
			audioDurationMs: 5_000,
			sourceStartMs: 1_000,
			sourceEndMs: 8_000,
			clipStartMs: 12_000,
			clipEndMs: 14_000
		})).toEqual({ sourceStartMs: 1_000, sourceEndMs: 5_000 });
	});
});
