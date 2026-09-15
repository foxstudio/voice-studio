import { describe, expect, it } from 'vitest';
import {
	mergeTaskPageWithPinned,
	stableTaskCardRenderLimit,
	taskIdentityKey,
	upsertTaskPreservingOrder
} from './task-page-merge';

describe('generation task page continuity', () => {
	it('keeps a just-submitted task visible when the current server filter omits it', () => {
		const current = [
			{ task_id: 'task-new', status: 'queued' },
			{ task_id: 'task-old', status: 'success' }
		];
		const page = [{ task_id: 'task-old', status: 'success' }];

		expect(mergeTaskPageWithPinned(page, current, new Set(['task-new']))).toEqual([
			{ task_id: 'task-new', status: 'queued' },
			{ task_id: 'task-old', status: 'success' }
		]);
	});

	it('uses the authoritative server item when the submitted task reaches the page', () => {
		const current = [{ task_id: 'task-new', status: 'queued' }];
		const page = [{ task_id: 'task-new', status: 'running' }];

		expect(mergeTaskPageWithPinned(page, current, new Set(['task-new']))).toEqual(page);
	});

	it('updates a polled task without moving it ahead of other queued tasks', () => {
		const current = [
			{ task_id: 'task-running', status: 'running' },
			{ task_id: 'task-queued', status: 'queued' },
			{ task_id: 'task-old', status: 'success' }
		];

		expect(upsertTaskPreservingOrder(current, { task_id: 'task-queued', status: 'running' })).toEqual([
			{ task_id: 'task-running', status: 'running' },
			{ task_id: 'task-queued', status: 'running' },
			{ task_id: 'task-old', status: 'success' }
		]);
	});

	it('prepends only a genuinely new task', () => {
		const current = [{ task_id: 'task-old', status: 'success' }];

		expect(upsertTaskPreservingOrder(current, { task_id: 'task-new', status: 'queued' })).toEqual([
			{ task_id: 'task-new', status: 'queued' },
			{ task_id: 'task-old', status: 'success' }
		]);
	});

	it('does not restart progressive card rendering for status-only updates', () => {
		const queued = [
			{ task_id: 'task-a', status: 'running' },
			{ task_id: 'task-b', status: 'queued' }
		];
		const updated = [
			{ task_id: 'task-a', status: 'success' },
			{ task_id: 'task-b', status: 'running' }
		];

		expect(taskIdentityKey(updated)).toBe(taskIdentityKey(queued));
		expect(stableTaskCardRenderLimit(10, 10)).toBe(10);
		expect(stableTaskCardRenderLimit(10, 11)).toBe(10);
		expect(stableTaskCardRenderLimit(0, 10)).toBe(4);
	});
});
