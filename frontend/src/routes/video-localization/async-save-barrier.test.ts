import { describe, expect, it } from 'vitest';
import { AsyncSaveBarrier } from './async-save-barrier';

describe('async save barrier', () => {
	it('waits for every save that was already in flight', async () => {
		const barrier = new AsyncSaveBarrier();
		let releaseFirst!: (value: boolean) => void;
		let releaseSecond!: (value: boolean) => void;
		barrier.track(new Promise<boolean>((resolve) => (releaseFirst = resolve)));
		barrier.track(new Promise<boolean>((resolve) => (releaseSecond = resolve)));

		let finished = false;
		const flushing = barrier.flush().then((value) => {
			finished = true;
			return value;
		});
		releaseFirst(true);
		await Promise.resolve();
		expect(finished).toBe(false);
		releaseSecond(true);

		expect(await flushing).toBe(true);
		expect(barrier.size).toBe(0);
	});

	it('reports a failed save instead of navigating with stale values', async () => {
		const barrier = new AsyncSaveBarrier();
		barrier.track(Promise.resolve(false));

		expect(await barrier.flush()).toBe(false);
	});
});
