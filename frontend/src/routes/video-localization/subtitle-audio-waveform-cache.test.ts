import { describe, expect, it, vi } from 'vitest';
import { SubtitleWaveformCache } from './subtitle-audio-waveform-cache';

const payload = (value: number) => ({ peaks: [value], duration: value });

describe('SubtitleWaveformCache', () => {
	it('reuses a completed waveform and refreshes its LRU position', async () => {
		const cache = new SubtitleWaveformCache(2);
		const loadA = vi.fn(async () => payload(1));

		expect(await cache.load('a', loadA)).toEqual(payload(1));
		expect(await cache.load('a', loadA)).toEqual(payload(1));
		await cache.load('b', async () => payload(2));
		cache.get('a');
		await cache.load('c', async () => payload(3));

		expect(loadA).toHaveBeenCalledTimes(1);
		expect(cache.get('a')).toEqual(payload(1));
		expect(cache.get('b')).toBeNull();
		expect(cache.size).toBe(2);
	});

	it('deduplicates concurrent requests for the same URL', async () => {
		const cache = new SubtitleWaveformCache();
		let resolveLoad: ((value: ReturnType<typeof payload>) => void) | undefined;
		const loader = vi.fn(() => new Promise<ReturnType<typeof payload>>((resolve) => {
			resolveLoad = resolve;
		}));

		const first = cache.load('shared', loader);
		const second = cache.load('shared', loader);
		expect(first).toBe(second);
		expect(cache.pendingCount).toBe(1);

		await Promise.resolve();
		expect(loader).toHaveBeenCalledTimes(1);
		resolveLoad?.(payload(4));
		await expect(Promise.all([first, second])).resolves.toEqual([payload(4), payload(4)]);
		expect(cache.pendingCount).toBe(0);
	});

	it('does not retain failed requests and allows a retry', async () => {
		const cache = new SubtitleWaveformCache();
		const loader = vi.fn()
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce(payload(5));

		await expect(cache.load('retry', loader)).rejects.toThrow('offline');
		expect(cache.pendingCount).toBe(0);
		await expect(cache.load('retry', loader)).resolves.toEqual(payload(5));
		expect(loader).toHaveBeenCalledTimes(2);
	});
});
