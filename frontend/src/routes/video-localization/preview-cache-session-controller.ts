import { Api } from '$lib/api';
import { ApiError } from '$lib/api/client';
import type { VideoPreviewCacheStatus } from '$lib/api/types';

export type PreviewCacheBuildRequest = {
	start_ms: number;
	end_ms: number;
	full?: boolean;
};

export type PreviewCacheSessionState = {
	cache: VideoPreviewCacheStatus | null;
	refreshing: boolean;
	refreshPending: boolean;
};

export type PreviewCacheRefreshResult =
	| { status: 'started' | 'completed'; message: string }
	| { status: 'failed'; error: string }
	| { status: 'stale' | 'busy' };

type PreviewCacheTransport = {
	buildVideoPreviewCache: (
		projectId: string,
		request: PreviewCacheBuildRequest
	) => Promise<VideoPreviewCacheStatus>;
	videoPreviewCacheStatus: (projectId: string) => Promise<VideoPreviewCacheStatus>;
	refreshVideoPreviewCache: (projectId: string) => Promise<VideoPreviewCacheStatus>;
};

type PreviewCacheSessionControllerOptions = {
	onStateChange?: (state: PreviewCacheSessionState) => void;
	onPlaybackCoverageInvalidated?: () => void;
};

class PreviewCacheRequestError extends Error {
	constructor(
		message: string,
		readonly kind: 'not_found' | 'timeout' | 'unknown'
	) {
		super(message);
		this.name = 'PreviewCacheRequestError';
	}
}

export class VideoLocalizationPreviewCacheClient {
	constructor(private readonly transport: PreviewCacheTransport = Api) {}

	build(projectId: string, request: PreviewCacheBuildRequest) {
		return this.request(() => this.transport.buildVideoPreviewCache(projectId, request));
	}

	status(projectId: string) {
		return this.request(() => this.transport.videoPreviewCacheStatus(projectId));
	}

	refresh(projectId: string) {
		return this.request(() => this.transport.refreshVideoPreviewCache(projectId));
	}

	private async request(operation: () => Promise<VideoPreviewCacheStatus>) {
		try {
			return await operation();
		} catch (error) {
			if (error instanceof ApiError) {
				throw new PreviewCacheRequestError(
					error.message,
					error.status === 404
						? 'not_found'
						: error.code === 'TIMEOUT'
							? 'timeout'
							: 'unknown'
				);
			}
			throw new PreviewCacheRequestError(
				error instanceof Error ? error.message : String(error),
				'unknown'
			);
		}
	}
}

export class PreviewCacheSessionController {
	private projectId = '';
	private generation = 0;
	private pollTimer: ReturnType<typeof setTimeout> | null = null;
	private pollInFlightGeneration: number | null = null;
	private requestedChunks = new Set<number>();
	private refreshRevision = '';
	private currentState: PreviewCacheSessionState = {
		cache: null,
		refreshing: false,
		refreshPending: false
	};

	constructor(
		private readonly client: VideoLocalizationPreviewCacheClient = new VideoLocalizationPreviewCacheClient(),
		private readonly options: PreviewCacheSessionControllerOptions = {}
	) {}

	get state(): PreviewCacheSessionState {
		return { ...this.currentState };
	}

	async activate(
		projectId: string,
		input: { sourceVideoAvailable: boolean; playheadMs: number }
	) {
		const generation = this.beginSession(projectId);
		this.replaceState({ cache: null, refreshing: false, refreshPending: false });
		if (!projectId || !input.sourceVideoAvailable) {
			return null;
		}
		try {
			const cache = await this.client.build(projectId, {
				start_ms: Math.max(0, input.playheadMs - 5_000),
				end_ms: input.playheadMs + 20_000,
				full: false
			});
			if (!this.isCurrent(projectId, generation)) return null;
			this.replaceState({ ...this.currentState, cache });
		} catch {
			if (!this.isCurrent(projectId, generation)) return null;
			this.replaceState({ ...this.currentState, cache: null });
		}
		if (this.isCurrent(projectId, generation)) this.schedulePoll(projectId, generation, 500);
		return this.currentState.cache;
	}

	async refresh(): Promise<PreviewCacheRefreshResult> {
		if (!this.projectId) return { status: 'stale' };
		if (this.currentState.refreshing) return { status: 'busy' };
		const projectId = this.projectId;
		const generation = this.restartGeneration();
		this.requestedChunks.clear();
		this.refreshRevision = '';
		this.options.onPlaybackCoverageInvalidated?.();
		this.replaceState({ ...this.currentState, refreshing: true, refreshPending: true });
		try {
			const cache = await this.client.refresh(projectId);
			if (!this.isCurrent(projectId, generation)) return { status: 'stale' };
			this.refreshRevision = cache.revision;
			this.replaceState({ ...this.currentState, cache });
			this.schedulePoll(projectId, generation, 400);
			return {
				status: 'started',
				message: '已重新加载播放器，正在重建预览画面'
			};
		} catch (error) {
			if (!this.isCurrent(projectId, generation)) return { status: 'stale' };
			if (error instanceof PreviewCacheRequestError && error.kind === 'timeout') {
				try {
					const cache = await this.client.status(projectId);
					if (!this.isCurrent(projectId, generation)) return { status: 'stale' };
					this.refreshRevision = cache.revision;
					const refreshPending = cache.state === 'building' || cache.ready_chunks === 0;
					this.replaceState({ ...this.currentState, cache, refreshPending });
					this.schedulePoll(projectId, generation, 400);
					return {
						status: refreshPending ? 'started' : 'completed',
						message: refreshPending
							? '缓存刷新已开始，正在重新生成'
							: '缓存刷新已完成'
					};
				} catch (statusError) {
					return this.failRefresh(statusError, projectId, generation);
				}
			}
			return this.failRefresh(error, projectId, generation);
		} finally {
			if (this.isCurrent(projectId, generation)) {
				this.replaceState({ ...this.currentState, refreshing: false });
			}
		}
	}

	requestAt(timeMs: number) {
		const cache = this.currentState.cache;
		if (!this.projectId || !cache) return false;
		const projectId = this.projectId;
		const generation = this.generation;
		const chunkMs = Math.max(1, cache.chunk_ms);
		const chunk = Math.floor(Math.max(0, timeMs) / chunkMs);
		if (this.requestedChunks.has(chunk)) return false;
		const range = cache.ranges[chunk];
		if (range?.status === 'ready' || range?.status === 'rendering') return false;
		this.requestedChunks.add(chunk);
		void this.client.build(projectId, {
			start_ms: chunk * chunkMs,
			end_ms: (chunk + 1) * chunkMs
		}).then((nextCache) => {
			if (!this.isCurrent(projectId, generation)) return;
			this.replaceState({ ...this.currentState, cache: nextCache });
			this.schedulePoll(projectId, generation, 350);
		}).catch(() => {
			if (this.isCurrent(projectId, generation)) this.requestedChunks.delete(chunk);
		});
		return true;
	}

	deactivate() {
		this.beginSession('');
		this.replaceState({ cache: null, refreshing: false, refreshPending: false });
		this.options.onPlaybackCoverageInvalidated?.();
	}

	dispose() {
		this.deactivate();
	}

	private beginSession(projectId: string) {
		this.projectId = projectId;
		const generation = this.restartGeneration();
		this.requestedChunks.clear();
		this.refreshRevision = '';
		return generation;
	}

	private restartGeneration() {
		this.generation += 1;
		if (this.pollTimer) clearTimeout(this.pollTimer);
		this.pollTimer = null;
		this.pollInFlightGeneration = null;
		return this.generation;
	}

	private schedulePoll(projectId: string, generation: number, delayMs: number) {
		if (!this.isCurrent(projectId, generation)) return;
		if (this.pollTimer) clearTimeout(this.pollTimer);
		this.pollTimer = setTimeout(() => void this.poll(projectId, generation), delayMs);
	}

	private async poll(projectId: string, generation: number) {
		if (
			!this.isCurrent(projectId, generation)
			|| this.pollInFlightGeneration === generation
		) return;
		this.pollInFlightGeneration = generation;
		let retryDelayMs: number | null = null;
		try {
			const cache = await this.client.status(projectId);
			if (!this.isCurrent(projectId, generation)) return;
			const refreshPending =
				this.currentState.refreshPending
				&& !(cache.revision === this.refreshRevision && cache.ready_chunks > 0);
			this.replaceState({
				...this.currentState,
				cache,
				refreshPending
			});
		} catch (error) {
			if (!this.isCurrent(projectId, generation)) return;
			if (error instanceof PreviewCacheRequestError && error.kind === 'not_found') {
				this.requestedChunks.clear();
				this.refreshRevision = '';
				this.replaceState({ cache: null, refreshing: false, refreshPending: false });
				this.options.onPlaybackCoverageInvalidated?.();
				this.stopPolling(generation);
				return;
			}
			this.options.onPlaybackCoverageInvalidated?.();
			retryDelayMs = 1_500;
		} finally {
			if (this.pollInFlightGeneration === generation) this.pollInFlightGeneration = null;
		}
		if (!this.isCurrent(projectId, generation)) return;
		this.schedulePoll(
			projectId,
			generation,
			retryDelayMs ?? 5_000
		);
	}

	private stopPolling(generation: number) {
		if (generation !== this.generation) return;
		if (this.pollTimer) clearTimeout(this.pollTimer);
		this.pollTimer = null;
	}

	private failRefresh(
		error: unknown,
		projectId: string,
		generation: number
	): PreviewCacheRefreshResult {
		if (!this.isCurrent(projectId, generation)) return { status: 'stale' };
		this.refreshRevision = '';
		this.replaceState({ ...this.currentState, refreshPending: false });
		return {
			status: 'failed',
			error: error instanceof Error && error.message
				? error.message
				: '刷新预览缓存失败'
		};
	}

	private isCurrent(projectId: string, generation: number) {
		return this.projectId === projectId && this.generation === generation;
	}

	private replaceState(state: PreviewCacheSessionState) {
		this.currentState = state;
		this.options.onStateChange?.(this.state);
	}
}
