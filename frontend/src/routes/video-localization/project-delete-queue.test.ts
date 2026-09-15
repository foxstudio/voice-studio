import { describe, expect, it, vi } from 'vitest';
import { ProjectDeleteQueue } from './project-delete-queue';

function deferred() {
	let resolve!: () => void;
	const promise = new Promise<void>((nextResolve) => {
		resolve = nextResolve;
	});
	return { promise, resolve };
}

describe('project delete queue', () => {
	it('runs confirmed deletions one at a time in click order', async () => {
		const first = deferred();
		const second = deferred();
		const started: string[] = [];
		const states: Array<{
			activeProjectId: string;
			queuedProjectIds: string[];
		}> = [];
		const queue = new ProjectDeleteQueue({
			onStateChange: (state) => states.push(state)
		});

		expect(queue.enqueue('project-a', async () => {
			started.push('project-a');
			await first.promise;
		})).toBe(true);
		expect(queue.enqueue('project-b', async () => {
			started.push('project-b');
			await second.promise;
		})).toBe(true);
		expect(queue.enqueue('project-c', async () => {
			started.push('project-c');
		})).toBe(true);

		expect(started).toEqual(['project-a']);
		expect(queue.state).toEqual({
			activeProjectId: 'project-a',
			queuedProjectIds: ['project-b', 'project-c']
		});

		first.resolve();
		await vi.waitFor(() => expect(started).toEqual([
			'project-a',
			'project-b'
		]));
		expect(queue.state).toEqual({
			activeProjectId: 'project-b',
			queuedProjectIds: ['project-c']
		});

		second.resolve();
		await queue.whenIdle();
		expect(started).toEqual(['project-a', 'project-b', 'project-c']);
		expect(queue.state).toEqual({
			activeProjectId: '',
			queuedProjectIds: []
		});
		expect(states.at(-1)).toEqual(queue.state);
	});

	it('deduplicates clicks and continues after one deletion fails', async () => {
		const errors: Array<[string, unknown]> = [];
		const started: string[] = [];
		const queue = new ProjectDeleteQueue({
			onError: (projectId, error) => errors.push([projectId, error])
		});
		const failure = new Error('delete failed');

		expect(queue.enqueue('project-a', async () => {
			started.push('project-a');
			throw failure;
		})).toBe(true);
		expect(queue.enqueue('project-a', async () => {
			started.push('duplicate');
		})).toBe(false);
		expect(queue.enqueue('project-b', async () => {
			started.push('project-b');
		})).toBe(true);

		await queue.whenIdle();

		expect(started).toEqual(['project-a', 'project-b']);
		expect(errors).toEqual([['project-a', failure]]);
	});
});
