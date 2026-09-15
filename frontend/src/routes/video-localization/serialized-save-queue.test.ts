import { describe, expect, it } from 'vitest';
import { SerializedSaveQueue } from './serialized-save-queue';

describe('serialized save queue', () => {
	it('does not let a later editorial mutation invalidate an earlier receipt', async () => {
		const queue = new SerializedSaveQueue();
		let releaseFirst!: () => void;
		const events: string[] = [];
		const first = queue.run(async () => {
			events.push('first:start');
			await new Promise<void>((resolve) => (releaseFirst = resolve));
			events.push('first:end');
			return 'first';
		});
		const second = queue.run(async () => {
			events.push('second:start');
			return 'second';
		});

		await Promise.resolve();
		expect(events).toEqual(['first:start']);
		releaseFirst();

		expect(await Promise.all([first, second])).toEqual(['first', 'second']);
		expect(events).toEqual(['first:start', 'first:end', 'second:start']);
	});

	it('continues after a failed write while preserving the rejection', async () => {
		const queue = new SerializedSaveQueue();
		const failed = queue.run(async () => { throw new Error('offline'); });
		const next = queue.run(async () => 'saved');

		await expect(failed).rejects.toThrow('offline');
		await expect(next).resolves.toBe('saved');
	});
});
