export type TtsWorkflowSessionContext = Readonly<{
	projectId: string;
	epoch: number;
	signal: AbortSignal;
}>;

export type TtsWorkflowSyncReason = 'load' | 'poll' | 'focus' | 'terminal' | 'manual';

type TimerHandle = ReturnType<typeof setTimeout>;

type TtsWorkflowSessionControllerOptions<Task> = {
	loadTasks: (
		context: TtsWorkflowSessionContext,
		reason: TtsWorkflowSyncReason
	) => Promise<Task[]>;
	taskId: (task: Task) => string;
	taskRevision?: (task: Task) => string;
	isActive: (task: Task) => boolean;
	isTerminal: (task: Task) => boolean;
	onTasks?: (
		context: TtsWorkflowSessionContext,
		tasks: Task[],
		reason: TtsWorkflowSyncReason
	) => void | Promise<void>;
	onTerminal?: (
		context: TtsWorkflowSessionContext,
		task: Task
	) => void | Promise<void>;
	onError?: (
		context: TtsWorkflowSessionContext,
		error: unknown,
		source: 'sync' | 'monitor'
	) => void;
	pollIntervalMs?: number;
	setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
	clearTimer?: (handle: TimerHandle) => void;
};

export type TtsWorkflowMonitorOptions<Result> = {
	fetch: (context: TtsWorkflowSessionContext) => Promise<Result>;
	isTerminal: (result: Result) => boolean;
	onUpdate?: (
		context: TtsWorkflowSessionContext,
		result: Result
	) => void | Promise<void>;
	onTerminal?: (
		context: TtsWorkflowSessionContext,
		result: Result
	) => void | Promise<void>;
	intervalMs?: number;
	maxAttempts?: number;
};

function defaultSetTimer(callback: () => void, delayMs: number) {
	return setTimeout(callback, delayMs);
}

function defaultClearTimer(handle: TimerHandle) {
	clearTimeout(handle);
}

function abortableDelay(delayMs: number, signal: AbortSignal) {
	if (signal.aborted || delayMs <= 0) return Promise.resolve();
	return new Promise<void>((resolve) => {
		const timer = setTimeout(finish, delayMs);
		function finish() {
			signal.removeEventListener('abort', finish);
			clearTimeout(timer);
			resolve();
		}
		signal.addEventListener('abort', finish, { once: true });
	});
}

/**
 * Owns the project-scoped lifetime of video-localization TTS requests.
 *
 * The controller never mutates page state directly. Every callback receives the
 * immutable project context that owned the request, and stale results are
 * dropped before callbacks run.
 */
export class TtsWorkflowSessionController<Task> {
	private readonly options: TtsWorkflowSessionControllerOptions<Task>;
	private epoch = 0;
	private current: TtsWorkflowSessionContext | null = null;
	private currentAbortController: AbortController | null = null;
	private pollTimer: TimerHandle | null = null;
	private pollRevision = 0;
	private readonly syncs = new Map<string, Promise<Task[] | null>>();
	private readonly monitors = new Map<string, AbortController>();
	private previousActiveTaskIds = new Set<string>();
	private terminalNotifiedTaskIds = new Set<string>();
	private lastTaskRevision = '';
	private visible = true;
	private disposed = false;

	constructor(options: TtsWorkflowSessionControllerOptions<Task>) {
		this.options = options;
	}

	activate(projectId: string, initialTasks: readonly Task[] = []) {
		if (!projectId) throw new Error('TTS workflow session requires a project id');
		this.endCurrentSession();
		this.disposed = false;
		this.epoch += 1;
		this.currentAbortController = new AbortController();
		this.current = Object.freeze({
			projectId,
			epoch: this.epoch,
			signal: this.currentAbortController.signal
		});
		this.previousActiveTaskIds = new Set(
			initialTasks.filter(this.options.isActive).map(this.options.taskId)
		);
		this.terminalNotifiedTaskIds = new Set();
		this.lastTaskRevision = '';
		return this.current;
	}

	deactivate() {
		this.endCurrentSession();
		this.epoch += 1;
		this.current = null;
	}

	captureContext() {
		return this.current;
	}

	isCurrent(context: TtsWorkflowSessionContext | null | undefined) {
		return Boolean(
			context
			&& !this.disposed
			&& !context.signal.aborted
			&& this.current
			&& context.projectId === this.current.projectId
			&& context.epoch === this.current.epoch
		);
	}

	async run<Result>(
		context: TtsWorkflowSessionContext,
		request: (context: TtsWorkflowSessionContext) => Promise<Result>,
		apply?: (context: TtsWorkflowSessionContext, result: Result) => void | Promise<void>
	): Promise<Result | null> {
		if (!this.isCurrent(context)) return null;
		const result = await request(context);
		if (!this.isCurrent(context)) return null;
		await apply?.(context, result);
		return this.isCurrent(context) ? result : null;
	}

	prepare<Result>(
		request: (context: TtsWorkflowSessionContext) => Promise<Result>,
		apply?: (context: TtsWorkflowSessionContext, result: Result) => void | Promise<void>
	) {
		const context = this.current;
		if (!context) return Promise.resolve(null);
		return this.run(context, request, apply);
	}

	sync(reason: TtsWorkflowSyncReason = 'manual') {
		const context = this.current;
		if (!context || !this.isCurrent(context)) return Promise.resolve(null);
		const pollRevision = this.pollRevision;
		const syncKey = `${context.epoch}:${pollRevision}`;
		const existing = this.syncs.get(syncKey);
		if (existing) return existing;
		const operation = this.performSync(context, reason, pollRevision).finally(() => {
			if (this.syncs.get(syncKey) === operation) this.syncs.delete(syncKey);
		});
		this.syncs.set(syncKey, operation);
		return operation;
	}

	startPolling(delayMs = 0) {
		const context = this.current;
		if (!this.visible || !context || !this.isCurrent(context)) return;
		this.invalidatePolling();
		this.schedulePoll(context, delayMs);
	}

	setVisible(visible: boolean) {
		if (this.visible === visible) return;
		this.visible = visible;
		if (!visible) {
			this.stopPolling();
			return;
		}
		// Task truth is independent from editable Draft truth. Always perform one
		// bounded task sync after returning to the page, even when the user has
		// unsaved timeline edits. The terminal callback can then merge only the
		// affected timeline projection without replacing those edits.
		this.startPolling(0);
	}

	stopPolling() {
		this.invalidatePolling();
	}

	async monitor<Result>(key: string, options: TtsWorkflowMonitorOptions<Result>) {
		const baseContext = this.current;
		if (!baseContext || !this.isCurrent(baseContext) || !key) return;
		this.cancelMonitor(key);
		const abortController = new AbortController();
		const abortMonitor = () => abortController.abort();
		baseContext.signal.addEventListener('abort', abortMonitor, { once: true });
		const context = Object.freeze({
			projectId: baseContext.projectId,
			epoch: baseContext.epoch,
			signal: abortController.signal
		});
		this.monitors.set(key, abortController);
		const intervalMs = Math.max(0, options.intervalMs ?? 1_000);
		const maxAttempts = Math.max(1, options.maxAttempts ?? Number.MAX_SAFE_INTEGER);
		try {
			for (let attempt = 0; attempt < maxAttempts && this.monitorIsCurrent(key, context, abortController); attempt += 1) {
				let result: Result;
				try {
					result = await options.fetch(context);
				} catch (error) {
					if (!this.monitorIsCurrent(key, context, abortController)) return;
					this.options.onError?.(context, error, 'monitor');
					await abortableDelay(intervalMs, context.signal);
					continue;
				}
				if (!this.monitorIsCurrent(key, context, abortController)) return;
				await options.onUpdate?.(context, result);
				if (!this.monitorIsCurrent(key, context, abortController)) return;
				if (options.isTerminal(result)) {
					await options.onTerminal?.(context, result);
					return;
				}
				await abortableDelay(intervalMs, context.signal);
			}
		} finally {
			baseContext.signal.removeEventListener('abort', abortMonitor);
			if (this.monitors.get(key) === abortController) this.monitors.delete(key);
		}
	}

	cancelMonitor(key: string) {
		const monitor = this.monitors.get(key);
		if (!monitor) return;
		monitor.abort();
		this.monitors.delete(key);
	}

	dispose() {
		this.disposed = true;
		this.endCurrentSession();
		this.epoch += 1;
		this.current = null;
		this.syncs.clear();
	}

	private async performSync(
		context: TtsWorkflowSessionContext,
		reason: TtsWorkflowSyncReason,
		pollRevision: number
	): Promise<Task[] | null> {
		try {
			const tasks = await this.options.loadTasks(context, reason);
			if (!this.isCurrent(context) || pollRevision !== this.pollRevision) return null;
			const taskRevision = this.options.taskRevision
				? tasks.map(this.options.taskRevision).join('|')
				: '';
			if (!this.options.taskRevision || taskRevision !== this.lastTaskRevision) {
				await this.options.onTasks?.(context, tasks, reason);
				this.lastTaskRevision = taskRevision;
			}
			if (!this.isCurrent(context) || pollRevision !== this.pollRevision) return null;
			for (const task of tasks) {
				const taskId = this.options.taskId(task);
				const transitionedToTerminal = this.previousActiveTaskIds.has(taskId)
					&& this.options.isTerminal(task);
				if (transitionedToTerminal && !this.terminalNotifiedTaskIds.has(taskId)) {
					this.terminalNotifiedTaskIds.add(taskId);
					await this.options.onTerminal?.(context, task);
					if (!this.isCurrent(context) || pollRevision !== this.pollRevision) return null;
				}
			}
			this.previousActiveTaskIds = new Set(
				tasks.filter(this.options.isActive).map(this.options.taskId)
			);
			if (this.visible && tasks.some(this.options.isActive)) {
				if (pollRevision === this.pollRevision) {
					this.schedulePoll(context, this.options.pollIntervalMs ?? 1_000);
				}
			} else if (pollRevision === this.pollRevision) {
				this.invalidatePolling();
			}
			return tasks;
		} catch (error) {
			if (!this.isCurrent(context) || pollRevision !== this.pollRevision) return null;
			this.options.onError?.(context, error, 'sync');
			if (this.visible && pollRevision === this.pollRevision) {
				this.schedulePoll(context, this.options.pollIntervalMs ?? 2_000);
			}
			return null;
		}
	}

	private schedulePoll(context: TtsWorkflowSessionContext, delayMs: number) {
		if (!this.visible || !this.isCurrent(context)) return;
		this.clearPollTimer();
		const pollRevision = this.pollRevision;
		this.pollTimer = (this.options.setTimer ?? defaultSetTimer)(() => {
			this.pollTimer = null;
			if (pollRevision === this.pollRevision && this.isCurrent(context)) void this.sync('poll');
		}, Math.max(0, delayMs));
	}

	private invalidatePolling() {
		this.pollRevision += 1;
		this.clearPollTimer();
	}

	private clearPollTimer() {
		if (!this.pollTimer) return;
		(this.options.clearTimer ?? defaultClearTimer)(this.pollTimer);
		this.pollTimer = null;
	}

	private monitorIsCurrent(
		key: string,
		context: TtsWorkflowSessionContext,
		abortController: AbortController
	) {
		return (
			this.monitors.get(key) === abortController
			&& !abortController.signal.aborted
			&& this.isCurrent(context)
		);
	}

	private endCurrentSession() {
		this.stopPolling();
		this.currentAbortController?.abort();
		this.currentAbortController = null;
		for (const monitor of this.monitors.values()) monitor.abort();
		this.monitors.clear();
	}
}
