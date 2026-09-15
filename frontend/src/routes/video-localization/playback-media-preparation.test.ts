import { describe, expect, it, vi } from 'vitest';

import { preparePlaybackMedia, restorePreparedMediaTime } from './playback-media-preparation';

describe('playback media preparation', () => {
	it('synchronizes and primes one target time before preparing only active missing media', () => {
		const ready = { id: 'ready' };
		const missing = { id: 'missing' };
		const syncAt = vi.fn();
		const primeUpcomingAt = vi.fn();
		const startPreparing = vi.fn();

		const active = preparePlaybackMedia({
			time: 12.5,
			syncAt,
			primeUpcomingAt,
			activeAt: vi.fn(() => [ready, missing, missing]),
			isReady: (media) => media === ready,
			startPreparing
		});

		expect(syncAt).toHaveBeenCalledWith(12.5);
		expect(primeUpcomingAt).toHaveBeenCalledWith(12.5);
		expect(active).toEqual([ready, missing]);
		expect(startPreparing).toHaveBeenCalledOnce();
		expect(startPreparing).toHaveBeenCalledWith(missing);
	});

	it('restores the desired position after loading resets a prepared media element', () => {
		const media = { currentTime: 0, duration: 600, readyState: 4 };

		expect(restorePreparedMediaTime(media, 432.25)).toBe(true);
		expect(media.currentTime).toBe(432.25);
		expect(restorePreparedMediaTime(media, 432.25)).toBe(false);
	});

	it('waits for metadata before restoring the desired position', () => {
		const media = { currentTime: 0, duration: Number.NaN, readyState: 0 };

		expect(restorePreparedMediaTime(media, 432.25)).toBe(false);
		expect(media.currentTime).toBe(0);
	});
});
