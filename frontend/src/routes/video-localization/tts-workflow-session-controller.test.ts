import { describe, expect, it, vi } from 'vitest';
import {
	TtsWorkflowSessionController,
	type TtsWorkflowSessionContext
} from './tts-workflow-session-controller';

type Task = {
	id: string;
	status: 'prepared' | 'queued' | 'running' | 'success' | 'failed' | 'cancelled';
};

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((nextResolve, nextReject) => {
		resolve = nextResolve;
		reject = nextReject;
	});
	return { promise, resolve, reject };
}

function controller(overrides: Partial<ConstructorParameters<typeof TtsWorkflowSessionController<Task>>[0]> = {}) {
	return new TtsWorkflowSessionController<Task>({
		loadTasks: async () => [],
		taskId: (task) => task.id,
		isActive: (task) => ['prepared', 'queued', 'running'].includes(task.status),
		isTerminal: (task) => ['success', 'failed', 'cancelled'].includes(task.status),
		...overrides
	});
}

describe('TTS workflow session controller', () => {
	it('activates immutable project contexts and invalidates them on deactivate', () => {
		const session = controller();
		const first = session.activate('project-a');
		expect(first).toMatchObject({ projectId: 'project-a', epoch: 1 });
		expect(Object.isFrozen(first)).toBe(true);
		expect(session.isCurrent(first)).toBe(true);

		session.deactivate();
		expect(first.signal.aborted).toBe(true);
		expect(session.isCurrent(first)).toBe(false);

		const second = session.activate('project-b');
		expect(second).toMatchObject({ projectId: 'project-b', epoch: 3 });
		expect(second.epoch).toBeGreaterThan(first.epoch);
		expect(session.isCurrent(second)).toBe(true);
	});

	it('captures the original project id and drops a stale prepare result', async () => {
		const response = deferred<string>();
		const applied: Array<{ projectId: string; result: string }> = [];
		const session = controller();
		session.activate('project-a');
		const pending = session.prepare(
			(context) => {
				expect(context.projectId).toBe('project-a');
				return response.promise;
			},
			(context, result) => {
				applied.push({ projectId: context.projectId, result });
			}
		);

		session.activate('project-b');
		response.resolve('workflow-a');

		expect(await pending).toBeNull();
		expect(applied).toEqual([]);
	});

	it('lets project B perform its first sync while project A sync is still in flight', async () => {
		const projectA = deferred<Task[]>();
		const projectB = deferred<Task[]>();
		const calls: string[] = [];
		const applied: string[] = [];
		const session = controller({
			loadTasks: (context) => {
				calls.push(context.projectId);
				return context.projectId === 'project-a' ? projectA.promise : projectB.promise;
			},
			onTasks: (context) => {
				applied.push(context.projectId);
			}
		});

		session.activate('project-a');
		const syncA = session.sync('load');
		session.activate('project-b');
		const syncB = session.sync('load');
		expect(calls).toEqual(['project-a', 'project-b']);

		projectB.resolve([{ id: 'b-1', status: 'queued' }]);
		expect(await syncB).toEqual([{ id: 'b-1', status: 'queued' }]);
		projectA.resolve([{ id: 'a-1', status: 'success' }]);
		expect(await syncA).toBeNull();
		expect(applied).toEqual(['project-b']);
	});

	it('lets a resumed session sync while a pre-pause sync is still in flight', async () => {
		const beforePause = deferred<Task[]>();
		const afterResume = deferred<Task[]>();
		let callCount = 0;
		const applied: string[] = [];
		const session = controller({
			loadTasks: () => {
				callCount += 1;
				return callCount === 1 ? beforePause.promise : afterResume.promise;
			},
			onTasks: (_context, _tasks, reason) => {
				applied.push(reason);
			}
		});

		session.activate('project-a');
		const staleSync = session.sync('poll');
		session.stopPolling();
		const resumedSync = session.sync('focus');
		expect(callCount).toBe(2);

		afterResume.resolve([{ id: 'a-1', status: 'running' }]);
		expect(await resumedSync).toEqual([{ id: 'a-1', status: 'running' }]);
		beforePause.resolve([{ id: 'a-0', status: 'running' }]);
		expect(await staleSync).toBeNull();
		expect(applied).toEqual(['focus']);
	});

	it('retries an initial task discovery failure even when no active task was cached', async () => {
		const timers: Array<() => void> = [];
		const onError = vi.fn();
		const session = controller({
			loadTasks: async () => {
				throw new Error('network unavailable');
			},
			onError,
			setTimer: (callback) => {
				timers.push(callback);
				return timers.length as unknown as ReturnType<typeof setTimeout>;
			},
			clearTimer: vi.fn()
		});
		session.activate('project-a', []);

		expect(await session.sync('load')).toBeNull();
		expect(onError).toHaveBeenCalledTimes(1);
		expect(timers).toHaveLength(1);
	});

	it('coalesces repeated queue wakeups into one project-level poll', async () => {
		vi.useFakeTimers();
		try {
			const loadTasks = vi.fn(async () => [{ id: 'task-1', status: 'running' as const }]);
			const session = controller({ loadTasks, pollIntervalMs: 1_000 });
			session.activate('project-a');

			for (let index = 0; index < 8; index += 1) session.startPolling(0);
			await vi.advanceTimersByTimeAsync(0);

			expect(loadTasks).toHaveBeenCalledTimes(1);
			session.dispose();
		} finally {
			vi.useRealTimers();
		}
	});

	it('drops stale monitor updates after switching projects', async () => {
		const response = deferred<Task>();
		const updates: string[] = [];
		const session = controller();
		session.activate('project-a');
		const monitor = session.monitor('task-a', {
			fetch: (context) => {
				expect(context.projectId).toBe('project-a');
				return response.promise;
			},
			isTerminal: (task) => task.status === 'success',
			onUpdate: (context) => {
				updates.push(context.projectId);
			},
			intervalMs: 0,
			maxAttempts: 1
		});

		session.activate('project-b');
		response.resolve({ id: 'task-a', status: 'success' });
		await monitor;
		expect(updates).toEqual([]);
	});

	it('runs terminal synchronization exactly once for one transition', async () => {
		let tasks: Task[] = [{ id: 'task-1', status: 'running' }];
		const terminal = vi.fn();
		const session = controller({
			loadTasks: async () => tasks,
			onTerminal: terminal
		});
		session.activate('project-a', tasks);

		tasks = [{ id: 'task-1', status: 'success' }];
		await session.sync('poll');
		await session.sync('focus');
		await session.sync('manual');

		expect(terminal).toHaveBeenCalledTimes(1);
		expect(terminal.mock.calls[0]?.[0]).toMatchObject({ projectId: 'project-a' });
		expect(terminal.mock.calls[0]?.[1]).toEqual({ id: 'task-1', status: 'success' });
	});

	it('resumes task synchronization after the page becomes visible even while content edits are pending', async () => {
		vi.useFakeTimers();
		try {
			let tasks: Task[] = [{ id: 'task-1', status: 'running' }];
			const terminal = vi.fn();
			const loadTasks = vi.fn(async () => tasks);
			const session = controller({ loadTasks, onTerminal: terminal, pollIntervalMs: 1_000 });
			session.activate('project-a', tasks);

			session.setVisible(false);
			tasks = [{ id: 'task-1', status: 'success' }];
			session.setVisible(true);
			await vi.advanceTimersByTimeAsync(0);

			expect(loadTasks).toHaveBeenCalledTimes(1);
			expect(terminal).toHaveBeenCalledTimes(1);
			expect(terminal.mock.calls[0]?.[1]).toEqual({ id: 'task-1', status: 'success' });
			session.dispose();
		} finally {
			vi.useRealTimers();
		}
	});

	it('does not keep scheduling task polls while the page is hidden', async () => {
		vi.useFakeTimers();
		try {
			const loadTasks = vi.fn(async () => [{ id: 'task-1', status: 'running' as const }]);
			const session = controller({ loadTasks, pollIntervalMs: 1_000 });
			session.activate('project-a', [{ id: 'task-1', status: 'running' }]);
			session.setVisible(false);
			session.startPolling(0);
			await vi.advanceTimersByTimeAsync(5_000);

			expect(loadTasks).not.toHaveBeenCalled();
			session.dispose();
		} finally {
			vi.useRealTimers();
		}
	});

	it('does not rebuild page task state when a poll returns the same task revisions', async () => {
		const onTasks = vi.fn();
		const session = controller({
			loadTasks: async () => [{ id: 'task-1', status: 'running' }],
			taskRevision: (task) => `${task.id}:${task.status}`,
			onTasks
		});
		session.activate('project-a');

		await session.sync('load');
		await session.sync('poll');

		expect(onTasks).toHaveBeenCalledTimes(1);
	});

	it('dispose aborts polling and prevents late sync and monitor callbacks', async () => {
		vi.useFakeTimers();
		try {
			const syncResponse = deferred<Task[]>();
			const monitorResponse = deferred<Task>();
			const onTasks = vi.fn();
			const onMonitorUpdate = vi.fn();
			const session = controller({
				loadTasks: () => syncResponse.promise,
				onTasks,
				pollIntervalMs: 10
			});
			const context = session.activate('project-a');
			session.startPolling(10);
			const sync = session.sync('load');
			const monitor = session.monitor('task-a', {
				fetch: () => monitorResponse.promise,
				isTerminal: (task) => task.status === 'success',
				onUpdate: onMonitorUpdate,
				intervalMs: 10
			});

			session.dispose();
			expect(context.signal.aborted).toBe(true);
			expect(session.captureContext()).toBeNull();
			expect(vi.getTimerCount()).toBe(0);

			syncResponse.resolve([{ id: 'task-a', status: 'success' }]);
			monitorResponse.resolve({ id: 'task-a', status: 'success' });
			expect(await sync).toBeNull();
			await monitor;
			expect(onTasks).not.toHaveBeenCalled();
			expect(onMonitorUpdate).not.toHaveBeenCalled();
		} finally {
			vi.useRealTimers();
		}
	});

	it('passes the same immutable context through a successful request and apply', async () => {
		const seen: TtsWorkflowSessionContext[] = [];
		const session = controller();
		const active = session.activate('project-a');
		const result = await session.run(
			active,
			async (context) => {
				seen.push(context);
				return 'prepared';
			},
			(context) => {
				seen.push(context);
			}
		);

		expect(result).toBe('prepared');
		expect(seen).toEqual([active, active]);
	});
});
