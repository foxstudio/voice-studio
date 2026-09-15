import { Api } from '$lib/api';
import { ApiError } from '$lib/api/client';
import type {
	VideoLocalizationAsrOperationRequest,
	VideoLocalizationDubSubtitleOperationRequest,
	VideoLocalizationLocalizationOperationRequest,
	VideoLocalizationOperation,
	VideoLocalizationOperationFeedV2
} from '$lib/api/types';
import { isActiveOperation, sortOperations, upsertOperation } from './utils';

type OperationFeedTransport = {
	videoLocalizationOperationFeedV2: (
		projectId: string,
		options?: {
			afterRevision?: number;
			cursor?: string;
			historyLimit?: number;
		}
	) => Promise<VideoLocalizationOperationFeedV2>;
	submitVideoLocalizationOperation: (
		projectId: string,
		kind: VideoLocalizationOperation['kind'],
		parameters: Record<string, unknown>
	) => Promise<VideoLocalizationOperation>;
	submitVideoLocalizationAsrOperation: (
		projectId: string,
		request: VideoLocalizationAsrOperationRequest
	) => Promise<VideoLocalizationOperation>;
	submitVideoLocalizationLocalizationOperation: (
		projectId: string,
		request: VideoLocalizationLocalizationOperationRequest
	) => Promise<VideoLocalizationOperation>;
	submitVideoLocalizationDubSubtitleOperation: (
		projectId: string,
		request: VideoLocalizationDubSubtitleOperationRequest
	) => Promise<VideoLocalizationOperation>;
	cancelVideoLocalizationOperation: (
		projectId: string,
		operationId: string
	) => Promise<VideoLocalizationOperation>;
	retryVideoLocalizationOperation: (
		projectId: string,
		operationId: string
	) => Promise<VideoLocalizationOperation>;
};

export type OperationFeedUpdateReason = 'load' | 'poll' | 'mutation' | 'history' | 'deactivate';

export type OperationHistoryState = {
	total: number;
	loaded: number;
	hasMore: boolean;
	loading: boolean;
};

type OperationFeedControllerOptions = {
	onOperations: (
		projectId: string,
		operations: VideoLocalizationOperation[],
		reason: OperationFeedUpdateReason
	) => void;
	refreshAfterTerminal: (projectId: string) => Promise<void>;
	onPollingSynchronized: (
		operations: VideoLocalizationOperation[],
		terminalTransition: VideoLocalizationOperation | null
	) => void;
	onTimeout: () => void;
	onError: (error: string) => void;
	onHistoryState?: (
		projectId: string,
		state: OperationHistoryState
	) => void;
	activePollMs?: number;
	idlePollMs?: number;
	historyPageSize?: number;
};

class OperationFeedRequestError extends Error {
	constructor(
		message: string,
		readonly kind: 'timeout' | 'unknown',
		readonly code = ''
	) {
		super(message);
		this.name = 'OperationFeedRequestError';
	}
}

export class VideoLocalizationOperationFeedClient {
	constructor(private readonly transport: OperationFeedTransport = Api) {}

	readHead(projectId: string, afterRevision?: number, historyLimit?: number) {
		return this.request(() =>
			this.transport.videoLocalizationOperationFeedV2(projectId, {
				afterRevision,
				historyLimit
			})
		);
	}

	readHistory(projectId: string, cursor: string, historyLimit?: number) {
		return this.request(() =>
			this.transport.videoLocalizationOperationFeedV2(projectId, {
				cursor,
				historyLimit
			})
		);
	}

	submit(
		projectId: string,
		kind: VideoLocalizationOperation['kind'],
		parameters: Record<string, unknown>
	) {
		return this.request(() => {
			if (kind === 'english_asr') {
				return this.transport.submitVideoLocalizationAsrOperation(
						projectId,
						parameters as VideoLocalizationAsrOperationRequest
					);
			}
			if (kind === 'localization_draft') {
				return this.transport.submitVideoLocalizationLocalizationOperation(
					projectId,
					parameters as VideoLocalizationLocalizationOperationRequest
				);
			}
			if (kind === 'dub_subtitle_generation') {
				return this.transport.submitVideoLocalizationDubSubtitleOperation(
					projectId,
					parameters as VideoLocalizationDubSubtitleOperationRequest
				);
			}
			return this.transport.submitVideoLocalizationOperation(projectId, kind, parameters);
		});
	}

	cancel(projectId: string, operationId: string) {
		return this.request(() =>
			this.transport.cancelVideoLocalizationOperation(projectId, operationId)
		);
	}

	retry(projectId: string, operationId: string) {
		return this.request(() =>
			this.transport.retryVideoLocalizationOperation(projectId, operationId)
		);
	}

	private async request<T>(operation: () => Promise<T>) {
		try {
			return await operation();
		} catch (error) {
			throw new OperationFeedRequestError(
				error instanceof Error ? error.message : String(error),
				error instanceof ApiError && error.code === 'TIMEOUT' ? 'timeout' : 'unknown',
				error instanceof ApiError ? error.code : ''
			);
		}
	}
}

export class OperationFeedController {
	private projectId = '';
	private generation = 0;
	private visible = true;
	private pollTimer: ReturnType<typeof setTimeout> | null = null;
	private pollInFlightGeneration: number | null = null;
	private currentOperations: VideoLocalizationOperation[] = [];
	private activeOperations: VideoLocalizationOperation[] = [];
	private historyOperations: VideoLocalizationOperation[] = [];
	private revision: number | null = null;
	private historyRevision: number | null = null;
	private historyTotalCount = 0;
	private nextHistoryCursor: string | null = null;
	private historyLoading = false;
	private readonly activePollMs: number;
	private readonly idlePollMs: number;
	private readonly historyPageSize: number;

	constructor(
		private readonly client: VideoLocalizationOperationFeedClient,
		private readonly options: OperationFeedControllerOptions
	) {
		this.activePollMs = options.activePollMs ?? 5_000;
		this.idlePollMs = options.idlePollMs ?? 5_000;
		this.historyPageSize = options.historyPageSize ?? 50;
	}

	get operations() {
		return [...this.currentOperations];
	}

	get historyState(): OperationHistoryState {
		return {
			total: this.historyTotalCount,
			loaded: this.historyOperations.length,
			hasMore: Boolean(this.nextHistoryCursor),
			loading: this.historyLoading
		};
	}

	async activate(projectId: string) {
		const generation = this.beginGeneration(projectId);
		this.replaceOperations([], 'load');
		if (!projectId) return null;
		return this.load(projectId, generation, true);
	}

	async loadProject(projectId: string) {
		if (projectId !== this.projectId) return this.activate(projectId);
		return this.reload();
	}

	async reload() {
		if (!this.projectId) return null;
		const projectId = this.projectId;
		const generation = this.restartGeneration();
		return this.load(projectId, generation, true);
	}

	async submit(
		projectId: string,
		kind: VideoLocalizationOperation['kind'],
		parameters: Record<string, unknown> = {}
	) {
		return this.mutate(
			projectId,
			(activeProjectId) => this.client.submit(activeProjectId, kind, parameters),
			0
		);
	}

	async cancel(projectId: string, operationId: string) {
		return this.mutate(
			projectId,
			(activeProjectId) => this.client.cancel(activeProjectId, operationId)
		);
	}

	async retry(projectId: string, operationId: string) {
		return this.mutate(
			projectId,
			(activeProjectId) => this.client.retry(activeProjectId, operationId)
		);
	}

	async loadMoreHistory() {
		if (
			!this.projectId
			|| !this.nextHistoryCursor
			|| this.historyLoading
		) return null;
		const projectId = this.projectId;
		const generation = this.generation;
		const cursor = this.nextHistoryCursor;
		this.historyLoading = true;
		this.emitHistoryState();
		try {
			const feed = await this.client.readHistory(
				projectId,
				cursor,
				this.historyPageSize
			);
			if (!this.isCurrent(projectId, generation)) return null;
			if (
				this.historyRevision !== null
				&& feed.history_revision !== this.historyRevision
			) {
				return this.reloadAfterHistoryInvalidation();
			}
			this.historyRevision = feed.history_revision;
			this.historyTotalCount = feed.history_total;
			this.nextHistoryCursor = feed.next_cursor;
			this.historyOperations = mergeOperations(
				this.historyOperations,
				feed.history
			).filter((operation) => !isActiveOperation(operation));
			this.publishCollections('history');
			return [...feed.history];
		} catch (error) {
			if (!this.isCurrent(projectId, generation)) return null;
			if (
				error instanceof OperationFeedRequestError
				&& error.code === 'VIDEO_LOCALIZATION_OPERATION_HISTORY_CURSOR_STALE'
			) {
				return this.reloadAfterHistoryInvalidation();
			}
			this.reportError(error);
			return null;
		} finally {
			if (this.isCurrent(projectId, generation)) {
				this.historyLoading = false;
				this.emitHistoryState();
			}
		}
	}

	setVisible(visible: boolean) {
		this.visible = visible;
		if (!visible) {
			this.clearPollTimer();
			return;
		}
		if (this.projectId) this.schedulePoll(this.projectId, this.generation, 0);
	}

	deactivate() {
		this.beginGeneration('');
		this.replaceOperations([], 'deactivate');
	}

	dispose() {
		this.deactivate();
	}

	private async load(projectId: string, generation: number, quiet: boolean) {
		try {
			const feed = await this.client.readHead(
				projectId,
				undefined,
				this.historyPageSize
			);
			if (!this.isCurrent(projectId, generation)) return null;
			this.revision = feed.revision;
			this.applyHeadFeed(feed, 'load');
			this.scheduleNextPoll(projectId, generation);
			return this.operations;
		} catch (error) {
			if (!this.isCurrent(projectId, generation)) return null;
			if (!quiet) this.reportError(error);
			this.schedulePoll(projectId, generation, 3_000);
			return null;
		}
	}

	private async mutate(
		projectId: string,
		request: (projectId: string) => Promise<VideoLocalizationOperation>,
		delayMs = this.activePollMs
	) {
		if (!projectId) return null;
		const switchingProject = projectId !== this.projectId;
		const generation = switchingProject
			? this.beginGeneration(projectId)
			: this.restartGeneration();
		if (switchingProject) this.replaceOperations([], 'load');
		let operation: VideoLocalizationOperation;
		try {
			operation = await request(projectId);
		} catch (error) {
			if (this.isCurrent(projectId, generation)) {
				this.schedulePoll(projectId, generation, 3_000);
			}
			throw error;
		}
		if (!this.isCurrent(projectId, generation)) return null;
		this.replaceOperations(
			upsertOperation(this.currentOperations, operation),
			'mutation'
		);
		this.schedulePoll(projectId, generation, delayMs);
		return operation;
	}

	private scheduleNextPoll(projectId: string, generation: number) {
		const delayMs = this.currentOperations.some(isActiveOperation)
			? this.activePollMs
			: this.idlePollMs;
		this.schedulePoll(projectId, generation, delayMs);
	}

	private schedulePoll(projectId: string, generation: number, delayMs: number) {
		if (!this.visible || !this.isCurrent(projectId, generation) || this.pollTimer) return;
		this.pollTimer = setTimeout(() => {
			this.pollTimer = null;
			void this.poll(projectId, generation);
		}, delayMs);
	}

	private async poll(projectId: string, generation: number) {
		if (!this.visible || !this.isCurrent(projectId, generation)) return;
		if (this.pollInFlightGeneration !== null) {
			this.schedulePoll(projectId, generation, 250);
			return;
		}
		this.pollInFlightGeneration = generation;
		let retryDelayMs: number | null = null;
		try {
			const previousById = new Map(
				this.currentOperations.map((operation) => [operation.operation_id, operation])
			);
			const feed = await this.client.readHead(
				projectId,
				this.revision ?? undefined,
				this.historyPageSize
			);
			if (!this.isCurrent(projectId, generation)) return;
			this.revision = feed.revision;
			if (!feed.changed) {
				this.options.onPollingSynchronized(this.operations, null);
			} else {
				this.applyHeadFeed(feed, 'poll');
				const terminalTransition = this.currentOperations.find((operation) => {
					const previous = previousById.get(operation.operation_id);
					return Boolean(previous && isActiveOperation(previous) && !isActiveOperation(operation));
				}) ?? null;
				if (terminalTransition) await this.options.refreshAfterTerminal(projectId);
				if (!this.isCurrent(projectId, generation)) return;
				this.options.onPollingSynchronized(this.operations, terminalTransition);
			}
		} catch (error) {
			if (!this.isCurrent(projectId, generation)) return;
			this.reportError(error);
			retryDelayMs = 3_000;
		} finally {
			if (this.pollInFlightGeneration === generation) this.pollInFlightGeneration = null;
		}
		if (!this.isCurrent(projectId, generation)) return;
		if (retryDelayMs !== null) {
			this.schedulePoll(projectId, generation, retryDelayMs);
		} else {
			this.scheduleNextPoll(projectId, generation);
		}
	}

	private reportError(error: unknown) {
		if (error instanceof OperationFeedRequestError && error.kind === 'timeout') {
			this.options.onTimeout();
			return;
		}
		this.options.onError(
			error instanceof Error && error.message
				? error.message
				: '刷新任务状态失败，正在重试'
		);
	}

	private reloadAfterHistoryInvalidation() {
		this.historyLoading = false;
		this.emitHistoryState();
		return this.reload();
	}

	private beginGeneration(projectId: string) {
		this.projectId = projectId;
		this.revision = null;
		this.historyRevision = null;
		this.historyTotalCount = 0;
		this.nextHistoryCursor = null;
		this.historyLoading = false;
		this.activeOperations = [];
		this.historyOperations = [];
		this.emitHistoryState();
		return this.restartGeneration();
	}

	private restartGeneration() {
		this.generation += 1;
		this.clearPollTimer();
		return this.generation;
	}

	private clearPollTimer() {
		if (this.pollTimer) clearTimeout(this.pollTimer);
		this.pollTimer = null;
	}

	private isCurrent(projectId: string, generation: number) {
		return this.projectId === projectId && this.generation === generation;
	}

	private replaceOperations(
		operations: VideoLocalizationOperation[],
		reason: OperationFeedUpdateReason
	) {
		const sorted = sortOperations(operations);
		this.activeOperations = sorted.filter(isActiveOperation);
		this.historyOperations = sorted.filter(
			(operation) => !isActiveOperation(operation)
		);
		this.historyTotalCount = Math.max(
			this.historyTotalCount,
			this.historyOperations.length
		);
		this.publishCollections(reason);
	}

	private applyHeadFeed(
		feed: VideoLocalizationOperationFeedV2,
		reason: Extract<OperationFeedUpdateReason, 'load' | 'poll'>
	) {
		const historyChanged = (
			this.historyRevision === null
			|| this.historyRevision !== feed.history_revision
		);
		this.activeOperations = sortOperations(feed.active_operations);
		this.historyOperations = historyChanged
			? sortOperations(feed.history)
			: mergeOperations(feed.history, this.historyOperations)
				.filter((operation) => !isActiveOperation(operation));
		this.historyRevision = feed.history_revision;
		this.historyTotalCount = feed.history_total;
		this.nextHistoryCursor = historyChanged
			? feed.next_cursor
			: this.nextHistoryCursor;
		this.publishCollections(reason);
	}

	private publishCollections(reason: OperationFeedUpdateReason) {
		this.currentOperations = sortOperations([
			...this.activeOperations,
			...this.historyOperations
		]);
		this.options.onOperations(this.projectId, this.operations, reason);
		this.emitHistoryState();
	}

	private emitHistoryState() {
		this.options.onHistoryState?.(
			this.projectId,
			this.historyState
		);
	}
}

function mergeOperations(
	primary: readonly VideoLocalizationOperation[],
	secondary: readonly VideoLocalizationOperation[]
) {
	const byId = new Map<string, VideoLocalizationOperation>();
	for (const operation of secondary) {
		byId.set(operation.operation_id, operation);
	}
	for (const operation of primary) {
		byId.set(operation.operation_id, operation);
	}
	return sortOperations([...byId.values()]);
}
