import type {
	VideoPlaybackProxyRequest,
	VideoPlaybackProxyStatus
} from '$lib/api/types';

export type PreviewVideoProxyState = {
	projectId: string;
	mediaRevision: string;
	status: 'idle' | 'building' | 'partial' | 'ready' | 'failed';
	mode: 'source' | 'segmented' | null;
	variant: 'source' | 'segments' | null;
	playable: boolean;
	profile: string;
	revision: string;
	durationMs: number;
	segmentMs: number;
	requestedRange: { start_ms: number; end_ms: number };
	readyRanges: Array<{ start_ms: number; end_ms: number }>;
	activeSegment: number | null;
	readySegments: number;
	totalSegments: number;
	progress: number;
	updatedAt: string | null;
	retryable: boolean;
	error: string | null;
	playbackStatus: VideoPlaybackProxyStatus | null;
};

type PreviewVideoProxyControllerOptions = {
	prepare: (
		projectId: string,
		request: VideoPlaybackProxyRequest
	) => Promise<VideoPlaybackProxyStatus>;
	probeSource?: (projectId: string, mediaRevision: string) => Promise<boolean>;
	onStateChange?: (state: PreviewVideoProxyState) => void;
	pollIntervalMs?: number;
	errorRetryMs?: number;
};

const EMPTY_STATE: PreviewVideoProxyState = {
	projectId: '',
	mediaRevision: '',
	status: 'idle',
	mode: null,
	variant: null,
	playable: false,
	profile: '',
	revision: '',
	durationMs: 0,
	segmentMs: 4_000,
	requestedRange: { start_ms: 0, end_ms: 0 },
	readyRanges: [],
	activeSegment: null,
	readySegments: 0,
	totalSegments: 0,
	progress: 0,
	updatedAt: null,
	retryable: false,
	error: null,
	playbackStatus: null
};

const PLAYBACK_PROXY_CONTRACT_VERSION = 'video-playback-proxy-status-v2';

class PlaybackProxyContractMismatchError extends Error {
	constructor() {
		super('页面与本地服务版本不一致，请重启 Voice Studio 后重新准备兼容画面。');
		this.name = 'PlaybackProxyContractMismatchError';
	}
}

/** Owns browser probing, prioritized proxy ranges, polling and stale-result rejection. */
export class PreviewVideoProxyController {
	private generation = 0;
	private timer: ReturnType<typeof setTimeout> | null = null;
	private currentState: PreviewVideoProxyState = { ...EMPTY_STATE };
	private readonly pollIntervalMs: number;
	private readonly errorRetryMs: number;
	private sourcePlayable = false;
	private targetStartMs = 0;

	constructor(private readonly options: PreviewVideoProxyControllerOptions) {
		this.pollIntervalMs = Math.max(50, options.pollIntervalMs ?? 800);
		this.errorRetryMs = Math.max(this.pollIntervalMs, options.errorRetryMs ?? 1_500);
	}

	get state(): PreviewVideoProxyState {
		return { ...this.currentState, readyRanges: [...this.currentState.readyRanges] };
	}

	activate(projectId: string, mediaRevision: string) {
		const generation = this.beginGeneration();
		this.sourcePlayable = false;
		this.targetStartMs = 0;
		if (!projectId || !mediaRevision) {
			this.replaceState({ ...EMPTY_STATE });
			return;
		}
		this.replaceState({
			...EMPTY_STATE,
			projectId,
			mediaRevision,
			status: 'building'
		});
		void this.probeAndPoll(projectId, mediaRevision, generation);
	}

	ensureRange(startMs: number) {
		const { projectId, mediaRevision } = this.currentState;
		if (!projectId || !mediaRevision) return;
		const nextTarget = Math.max(0, Math.round(startMs));
		const segmentMs = Math.max(1, this.currentState.segmentMs);
		const sameSegment = (
			Math.floor(nextTarget / segmentMs)
			=== Math.floor(this.targetStartMs / segmentMs)
		);
		this.targetStartMs = nextTarget;
		if (
			this.sourcePlayable
			|| this.currentState.mode !== 'segmented'
			|| (sameSegment && this.currentState.playable)
		) return;
		const generation = this.beginGeneration();
		void this.poll(projectId, mediaRevision, generation);
	}

	retry() {
		const { projectId, mediaRevision } = this.currentState;
		if (!projectId || !mediaRevision) return;
		this.activate(projectId, mediaRevision);
	}

	dispose() {
		this.beginGeneration();
		this.replaceState({ ...EMPTY_STATE });
	}

	private async probeAndPoll(
		projectId: string,
		mediaRevision: string,
		generation: number
	) {
		try {
			this.sourcePlayable = this.options.probeSource
				? await this.options.probeSource(projectId, mediaRevision)
				: false;
		} catch {
			this.sourcePlayable = false;
		}
		if (!this.isCurrent(projectId, mediaRevision, generation)) return;
		await this.poll(projectId, mediaRevision, generation);
	}

	private async poll(
		projectId: string,
		mediaRevision: string,
		generation: number
	) {
		try {
			const request: VideoPlaybackProxyRequest = {
				source_playable: this.sourcePlayable,
				start_ms: this.targetStartMs,
				fill_background: true
			};
			const result = await this.options.prepare(projectId, request);
			if (!this.isCurrent(projectId, mediaRevision, generation)) return;
			assertSupportedPlaybackProxyContract(result);
			this.replaceState(fromStatus(projectId, mediaRevision, result));
			if (result.mode === 'source' || result.state === 'ready') return;
			if (result.state === 'failed' && !result.playable) return;
			this.schedule(projectId, mediaRevision, generation, this.pollIntervalMs);
		} catch (error) {
			if (!this.isCurrent(projectId, mediaRevision, generation)) return;
			const contractMismatch = error instanceof PlaybackProxyContractMismatchError;
			this.replaceState({
				...this.currentState,
				status: contractMismatch ? 'failed' : 'building',
				retryable: contractMismatch || this.currentState.retryable,
				error: error instanceof Error ? error.message : String(error)
			});
			if (contractMismatch) return;
			this.schedule(projectId, mediaRevision, generation, this.errorRetryMs);
		}
	}

	private schedule(
		projectId: string,
		mediaRevision: string,
		generation: number,
		delayMs: number
	) {
		if (!this.isCurrent(projectId, mediaRevision, generation)) return;
		if (this.timer) clearTimeout(this.timer);
		this.timer = setTimeout(() => {
			this.timer = null;
			void this.poll(projectId, mediaRevision, generation);
		}, delayMs);
	}

	private beginGeneration() {
		this.generation += 1;
		if (this.timer) clearTimeout(this.timer);
		this.timer = null;
		return this.generation;
	}

	private isCurrent(projectId: string, mediaRevision: string, generation: number) {
		return generation === this.generation
			&& projectId === this.currentState.projectId
			&& mediaRevision === this.currentState.mediaRevision;
	}

	private replaceState(state: PreviewVideoProxyState) {
		this.currentState = state;
		this.options.onStateChange?.(this.state);
	}
}

function assertSupportedPlaybackProxyContract(result: VideoPlaybackProxyStatus) {
	const contractVersion = (result as { contract_version?: unknown }).contract_version;
	if (contractVersion !== PLAYBACK_PROXY_CONTRACT_VERSION) {
		throw new PlaybackProxyContractMismatchError();
	}
}

function fromStatus(
	projectId: string,
	mediaRevision: string,
	result: VideoPlaybackProxyStatus
): PreviewVideoProxyState {
	return {
		projectId,
		mediaRevision,
		status: result.state,
		mode: result.mode,
		variant: result.variant,
		playable: result.playable,
		profile: result.profile,
		revision: result.revision,
		durationMs: result.duration_ms,
		segmentMs: result.segment_ms,
		requestedRange: result.requested_range,
		readyRanges: result.ready_ranges,
		activeSegment: result.active_segment,
		readySegments: result.ready_segments,
		totalSegments: result.total_segments,
		progress: result.progress,
		updatedAt: result.updated_at,
		retryable: result.retryable,
		error: result.error,
		playbackStatus: result
	};
}
