import { afterEach, describe, expect, it, vi } from 'vitest';
import { cachedCoveringWaveform, requestWaveform, waveformResourceIdentity, waveformUrlWithBins } from './waveform-peaks-client';

afterEach(() => vi.unstubAllGlobals());

describe('waveform peaks client', () => {
	it('adds an adaptive bin request without losing existing query parameters', () => {
		expect(waveformUrlWithBins('/api/voices/files/file-1/waveform', 4_800))
			.toBe('/api/voices/files/file-1/waveform?bins=4800');
		expect(waveformUrlWithBins('/api/waveform?v=2', 64))
			.toBe('/api/waveform?v=2&bins=64');
	});

	it('separates immutable media identity from transient waveform tiles', () => {
		expect(waveformResourceIdentity('/api/timeline/clip/waveform?v=result-1&bins=4800&start_ms=1000&end_ms=5000'))
			.toBe('/api/timeline/clip/waveform?v=result-1');
		expect(waveformResourceIdentity('/api/timeline/clip/waveform?v=result-2&bins=320'))
			.toBe('/api/timeline/clip/waveform?v=result-2');
		expect(waveformResourceIdentity('/api/timeline/clip/waveform?v=result-1&recovery=2&bins=320'))
			.toBe('/api/timeline/clip/waveform?v=result-1&recovery=2');
	});

	it('reuses sufficiently detailed source coverage for newly mounted split children', async () => {
		const payload = { peaks: Array(1024).fill(0.25), duration: 10, bins: 1024, window_start_ms: 1000, window_end_ms: 5000 };
		const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
		vi.stubGlobal('fetch', fetcher);
		const base = '/api/cover-split/waveform?v=same';
		await requestWaveform(`${base}&bins=1024&start_ms=1000&end_ms=5000`, new AbortController().signal);
		expect(cachedCoveringWaveform(`${base}&bins=512&start_ms=3000&end_ms=5000`)).toBe(payload);
		expect(fetcher).toHaveBeenCalledTimes(1);
		for (const suffix of [
			'&bins=512&start_ms=500&end_ms=2500',
			'&bins=512&start_ms=3500&end_ms=5500',
			'&bins=1024&start_ms=3000&end_ms=5000',
			'&bins=320', '&bins=0&start_ms=3000&end_ms=5000'
		]) expect(cachedCoveringWaveform(base + suffix)).toBeNull();
		expect(cachedCoveringWaveform(`${base.replace('same', 'changed')}&bins=512&start_ms=3000&end_ms=5000`)).toBeNull();
		expect(cachedCoveringWaveform(`${base}&recovery=1&bins=512&start_ms=3000&end_ms=5000`)).toBeNull();
	});
	it('does not promote a coarse whole-file preview to detailed source coverage', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ peaks: Array(320).fill(0.2), duration: 20, bins: 320 }) }));
		await requestWaveform('/api/coarse-preview/waveform?v=same&bins=320', new AbortController().signal);
		expect(cachedCoveringWaveform('/api/coarse-preview/waveform?v=same&bins=1024&start_ms=1000&end_ms=2000')).toBeNull();
	});
});
