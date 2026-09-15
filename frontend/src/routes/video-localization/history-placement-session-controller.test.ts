import { describe, expect, it } from 'vitest';
import { HistoryPlacementSessionController } from './history-placement-session-controller';

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}

function operation<T>(request: () => Promise<T>, onSettled: () => void) {
	return { currentSave: Promise.resolve(), request, applyReceipt: () => true, refresh: async () => {}, onApplied() {}, onSettled };
}

describe('HistoryPlacementSessionController', () => {
	it('holds timeline persistence until every in-flight history placement settles', async () => {
		const controller = new HistoryPlacementSessionController(() => 'project');
		const first = deferred<void>();
		const second = deferred<void>();
		const events: string[] = [];

		const firstTracked = controller.execute(controller.capture('clip-1'), operation(() => first.promise, () => events.push('first-settled')));
		const secondTracked = controller.execute(controller.capture('clip-2'), operation(() => second.promise, () => events.push('second-settled')));
		const waiting = controller.waitForPending().then(() => events.push('released'));

		await Promise.resolve();
		expect(controller.hasPending).toBe(true);
		expect(events).toEqual([]);

		first.resolve();
		await firstTracked;
		expect(events).toEqual(['first-settled']);

		second.resolve();
		await secondTracked;
		await waiting;
		expect(controller.hasPending).toBe(false);
		expect(events).toEqual(['first-settled', 'second-settled', 'released']);
	});

	it.each(['null', 'failure'])('always settles %s commands before releasing persistence', async (kind) => {
		const controller = new HistoryPlacementSessionController(() => 'project');
		const events: string[] = [];
		const pending = controller.execute(controller.capture('request'), operation(async () => {
			if (kind === 'failure') throw new Error('conflict');
			return null;
		}, () => events.push('cleanup')));
		if (kind === 'failure') await expect(pending).rejects.toThrow('conflict');
		else await expect(pending).resolves.toBeNull();
		await controller.waitForPending();
		expect(events).toEqual(['cleanup']);
		expect(controller.hasPending).toBe(false);
	});

	it('does not run or settle an old session after leaving and reopening the same project', async () => {
		const controller = new HistoryPlacementSessionController(() => 'project');
		const context = controller.capture('request');
		const events: string[] = [];
		const pending = controller.execute(context, operation(async () => events.push('POST'), () => events.push('cleanup-new-page')));
		controller.reset();
		await expect(pending).resolves.toBeNull();
		expect(events).toEqual([]);
		expect(controller.isCurrent(context)).toBe(false);
	});
});
