import type { VideoPlaybackProxyStatus } from '$lib/api/types';

const VIDEO_MIME = 'video/mp4; codecs="avc1.640028"';
const LOOK_BEHIND_SEGMENTS = 1;
const LOOK_AHEAD_SEGMENTS = 3;

type SegmentedVideoSourceOptions = {
	onSourceChange: (sourceUrl: string) => void;
	onError?: (message: string) => void;
};

type PendingAppend = {
	index: number;
	bytes: ArrayBuffer;
};

/** Owns one sparse MSE video timeline. Audio remains in the existing track nodes. */
export class SegmentedVideoSourceController {
	private mediaSource: MediaSource | null = null;
	private sourceBuffer: SourceBuffer | null = null;
	private objectUrl = '';
	private projectId = '';
	private revision = '';
	private status: VideoPlaybackProxyStatus | null = null;
	private requested = new Set<number>();
	private appended = new Set<number>();
	private appendQueue: PendingAppend[] = [];
	private fetching = false;
	private generation = 0;

	constructor(private readonly options: SegmentedVideoSourceOptions) {}

	activate(projectId: string, status: VideoPlaybackProxyStatus) {
		if (
			this.mediaSource
			&& this.projectId === projectId
			&& this.revision === status.revision
		) {
			this.update(status);
			return this.objectUrl;
		}
		this.dispose();
		if (
			typeof MediaSource === 'undefined'
			|| !MediaSource.isTypeSupported(VIDEO_MIME)
		) {
			this.options.onError?.('当前浏览器不支持分段视频播放');
			return '';
		}
		this.projectId = projectId;
		this.revision = status.revision;
		this.status = status;
		const generation = this.generation;
		const mediaSource = new MediaSource();
		this.mediaSource = mediaSource;
		this.objectUrl = URL.createObjectURL(mediaSource);
		mediaSource.addEventListener('sourceopen', () => {
			if (generation !== this.generation || this.mediaSource !== mediaSource) return;
			this.openSourceBuffer();
		}, { once: true });
		this.options.onSourceChange(this.objectUrl);
		return this.objectUrl;
	}

	update(status: VideoPlaybackProxyStatus) {
		if (status.mode !== 'segmented' || status.revision !== this.revision) return;
		this.status = status;
		if (this.mediaSource?.readyState === 'open' && status.duration_ms > 0) {
			this.mediaSource.duration = status.duration_ms / 1000;
		}
		this.queueReadySegments();
	}

	dispose() {
		this.generation += 1;
		this.mediaSource = null;
		this.sourceBuffer = null;
		this.status = null;
		this.projectId = '';
		this.revision = '';
		this.requested.clear();
		this.appended.clear();
		this.appendQueue = [];
		this.fetching = false;
		if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
		this.objectUrl = '';
		this.options.onSourceChange('');
	}

	private openSourceBuffer() {
		if (!this.mediaSource || this.mediaSource.readyState !== 'open') return;
		try {
			const sourceBuffer = this.mediaSource.addSourceBuffer(VIDEO_MIME);
			sourceBuffer.mode = 'sequence';
			this.sourceBuffer = sourceBuffer;
			if (this.status?.duration_ms) {
				this.mediaSource.duration = this.status.duration_ms / 1000;
			}
			sourceBuffer.addEventListener('updateend', () => this.appendNext());
			this.queueReadySegments();
		} catch (error) {
			this.options.onError?.(error instanceof Error ? error.message : String(error));
		}
	}

	private queueReadySegments() {
		if (!this.status || !this.sourceBuffer) return;
		const desiredIndexes = new Set(segmentIndexesForStatus(this.status));
		for (const index of [...this.requested]) {
			if (!desiredIndexes.has(index)) this.requested.delete(index);
		}
		this.appendQueue = this.appendQueue.filter((item) => desiredIndexes.has(item.index));
		for (const index of [...this.appended]) {
			if (!desiredIndexes.has(index)) this.appended.delete(index);
		}
		for (const index of desiredIndexes) {
			if (this.appended.has(index) || this.requested.has(index)) continue;
			this.requested.add(index);
		}
		this.appendNext();
		void this.fetchNext();
	}

	private async fetchNext() {
		if (this.fetching || !this.status) return;
		const next = [...this.requested].find((index) => (
			!this.appended.has(index)
			&& !this.appendQueue.some((item) => item.index === index)
		));
		if (next === undefined) return;
		this.fetching = true;
		const generation = this.generation;
		try {
			const response = await fetch(segmentUrl(this.projectId, next, this.revision));
			if (!response.ok) throw new Error(`分段视频读取失败（${response.status}）`);
			const bytes = await response.arrayBuffer();
			if (generation !== this.generation) return;
			if (!segmentIndexesForStatus(this.status).includes(next)) {
				this.requested.delete(next);
				return;
			}
			this.appendQueue.push({ index: next, bytes });
			this.appendNext();
		} catch (error) {
			this.requested.delete(next);
			this.options.onError?.(error instanceof Error ? error.message : String(error));
		} finally {
			this.fetching = false;
			if (generation === this.generation) void this.fetchNext();
		}
	}

	private appendNext() {
		const sourceBuffer = this.sourceBuffer;
		const status = this.status;
		if (!sourceBuffer || !status || sourceBuffer.updating) return;
		if (evictOutsideRetention(sourceBuffer, status)) return;
		const desiredIndexes = new Set(segmentIndexesForStatus(status));
		let pending = this.appendQueue.shift();
		while (pending && !desiredIndexes.has(pending.index)) {
			this.requested.delete(pending.index);
			pending = this.appendQueue.shift();
		}
		if (!pending) return;
		const startSeconds = pending.index * status.segment_ms / 1000;
		const endSeconds = Math.min(
			status.duration_ms / 1000,
			startSeconds + status.segment_ms / 1000
		);
		try {
			configureAppendWindow(sourceBuffer, startSeconds, endSeconds);
			sourceBuffer.appendBuffer(pending.bytes);
			this.appended.add(pending.index);
			this.requested.delete(pending.index);
		} catch (error) {
			this.requested.delete(pending.index);
			this.options.onError?.(error instanceof Error ? error.message : String(error));
			queueMicrotask(() => this.appendNext());
		}
	}
}

export function configureAppendWindow(
	sourceBuffer: Pick<SourceBuffer, 'timestampOffset' | 'appendWindowStart' | 'appendWindowEnd'>,
	startSeconds: number,
	endSeconds: number
) {
	sourceBuffer.timestampOffset = startSeconds;
	if (endSeconds <= sourceBuffer.appendWindowStart) {
		// Sparse prefetch can return to an earlier segment after reading ahead.
		// Lower the start first so Chrome never sees end <= start.
		sourceBuffer.appendWindowStart = startSeconds;
		sourceBuffer.appendWindowEnd = endSeconds;
		return;
	}
	// Forward appends must expand the end before advancing the start.
	sourceBuffer.appendWindowEnd = endSeconds;
	sourceBuffer.appendWindowStart = startSeconds;
}

export function segmentIndexesForStatus(status: VideoPlaybackProxyStatus) {
	if (status.mode !== 'segmented' || status.segment_ms <= 0) return [];
	const target = Math.floor(status.requested_range.start_ms / status.segment_ms);
	const first = Math.max(0, target - LOOK_BEHIND_SEGMENTS);
	const last = Math.min(status.total_segments - 1, target + LOOK_AHEAD_SEGMENTS);
	const indexes: number[] = [];
	const candidates = [
		target,
		...Array.from({ length: Math.max(0, last - target) }, (_, offset) => target + offset + 1),
		...Array.from({ length: Math.max(0, target - first) }, (_, offset) => target - offset - 1)
	];
	for (const index of candidates) {
		const startMs = index * status.segment_ms;
		const endMs = Math.min(status.duration_ms, startMs + status.segment_ms);
		if (status.ready_ranges.some((range) => range.start_ms <= startMs && range.end_ms >= endMs)) {
			indexes.push(index);
		}
	}
	return indexes;
}

export function segmentRetentionRange(status: VideoPlaybackProxyStatus) {
	if (status.mode !== 'segmented' || status.segment_ms <= 0) {
		return { startSeconds: 0, endSeconds: Math.max(0, status.duration_ms / 1000) };
	}
	const target = Math.floor(status.requested_range.start_ms / status.segment_ms);
	const first = Math.max(0, target - LOOK_BEHIND_SEGMENTS);
	const last = Math.min(status.total_segments - 1, target + LOOK_AHEAD_SEGMENTS);
	return {
		startSeconds: first * status.segment_ms / 1000,
		endSeconds: Math.min(status.duration_ms, (last + 1) * status.segment_ms) / 1000
	};
}

function evictOutsideRetention(sourceBuffer: SourceBuffer, status: VideoPlaybackProxyStatus) {
	const keep = segmentRetentionRange(status);
	const epsilon = 0.001;
	for (let index = 0; index < sourceBuffer.buffered.length; index += 1) {
		const start = sourceBuffer.buffered.start(index);
		const end = sourceBuffer.buffered.end(index);
		if (start < keep.startSeconds - epsilon) {
			sourceBuffer.remove(start, Math.min(end, keep.startSeconds));
			return true;
		}
		if (end > keep.endSeconds + epsilon) {
			sourceBuffer.remove(Math.max(start, keep.endSeconds), end);
			return true;
		}
	}
	return false;
}

export function segmentUrl(projectId: string, segmentIndex: number, revision: string) {
	return `/api/projects/${encodeURIComponent(projectId)}/video-localization/source-media/preview-video/segments/${Math.max(0, Math.floor(segmentIndex))}?revision=${encodeURIComponent(revision)}`;
}
