import type { VideoPreviewCacheRange } from '$lib/api/types';

export type PlaybackReadinessRangeStatus = 'empty' | 'loading' | 'ready' | 'failed';

export type PlaybackBufferedRange = {
	start_ms: number;
	end_ms: number;
};

export type PlaybackMediaRequirement = {
	id: string;
	label: string;
	timeline_start_ms: number;
	timeline_end_ms: number;
	source_start_ms: number;
	source_end_ms: number;
	media_duration_ms: number | null;
	buffered: PlaybackBufferedRange[];
	loading: boolean;
	pending_timeline_ms?: number | null;
	error: boolean;
};

export type PlaybackReadinessRange = {
	start_ms: number;
	end_ms: number;
	status: PlaybackReadinessRangeStatus;
	blockers: string[];
};

export type PlaybackReadinessStatus = {
	state: 'empty' | 'building' | 'partial' | 'ready' | 'failed';
	ready_chunks: number;
	total_chunks: number;
	progress: number;
	ranges: PlaybackReadinessRange[];
};

export function pendingPlaybackRanges(previewRanges: VideoPreviewCacheRange[]): PlaybackReadinessRange[] {
	return previewRanges.map((range) => ({
		start_ms: range.start_ms,
		end_ms: range.end_ms,
		status: range.status === 'failed' ? 'failed' : range.status === 'empty' ? 'empty' : 'loading',
		blockers: [range.status === 'ready' ? '正在检测视频与音频' : '预览画面']
	}));
}

const BUFFER_TOLERANCE_MS = 50;

/** Missing/idle elements are waiting for playback, not fetching media. */
export function playbackMediaIsLoading(media: Pick<HTMLMediaElement, 'networkState' | 'readyState'> | null): boolean {
	// NETWORK_LOADING; keep this helper usable without a browser global.
	return media?.networkState === 2 && media.readyState < 1;
}

export function buildPlaybackReadiness(input: {
	durationMs: number;
	chunkMs: number;
	previewRanges: VideoPreviewCacheRange[];
	media: PlaybackMediaRequirement[];
}): PlaybackReadinessStatus {
	const durationMs = Math.max(0, Math.round(input.durationMs));
	const chunkMs = Math.max(1, Math.round(input.chunkMs));
	const totalChunks = durationMs ? Math.ceil(durationMs / chunkMs) : 0;
	const ranges: PlaybackReadinessRange[] = [];

	for (let index = 0; index < totalChunks; index += 1) {
		const startMs = index * chunkMs;
		const endMs = Math.min(durationMs, startMs + chunkMs);
		const preview = input.previewRanges[index];
		const blockers: string[] = [];
		let status: PlaybackReadinessRangeStatus = 'ready';

		if (!preview || preview.status === 'empty') {
			status = 'empty';
			blockers.push('预览画面');
		} else if (preview.status === 'rendering') {
			status = 'loading';
			blockers.push('预览画面');
		} else if (preview.status === 'failed') {
			status = 'failed';
			blockers.push('预览画面');
		}

		for (const requirement of input.media) {
			const overlapStart = Math.max(startMs, requirement.timeline_start_ms);
			const overlapEnd = Math.min(endMs, requirement.timeline_end_ms);
			if (overlapEnd <= overlapStart) continue;
			const pendingHere = requirement.pending_timeline_ms != null
				&& requirement.pending_timeline_ms >= overlapStart
				&& requirement.pending_timeline_ms < overlapEnd;
			const sourceStart = requirement.source_start_ms + overlapStart - requirement.timeline_start_ms;
			const requiredSourceEnd = requirement.source_start_ms + overlapEnd - requirement.timeline_start_ms;
			const availableSourceEnd = requirement.media_duration_ms == null
				? requirement.source_end_ms
				: Math.min(requirement.source_end_ms, requirement.media_duration_ms);
			if (requiredSourceEnd > availableSourceEnd + BUFFER_TOLERANCE_MS) {
				blockers.push(`${requirement.label}片段长度超过素材`);
				status = 'failed';
				continue;
			}
			const sourceEnd = Math.min(availableSourceEnd, requiredSourceEnd);
			if (sourceEnd <= sourceStart) continue;
			if (bufferedRangeCovered(requirement.buffered, sourceStart, sourceEnd) && !requirement.loading && !pendingHere) continue;
			blockers.push(requirement.label);
			if (requirement.error) status = 'failed';
			else if (status !== 'failed' && (requirement.loading || pendingHere)) status = 'loading';
			else if (status === 'ready') status = 'empty';
		}

		ranges.push({ start_ms: startMs, end_ms: endMs, status, blockers: [...new Set(blockers)] });
	}

	const readyChunks = ranges.filter((range) => range.status === 'ready').length;
	const failed = ranges.some((range) => range.status === 'failed');
	const loading = ranges.some((range) => range.status === 'loading');
	return {
		state: failed ? 'failed' : readyChunks === totalChunks && totalChunks > 0 ? 'ready' : loading ? 'building' : readyChunks ? 'partial' : 'empty',
		ready_chunks: readyChunks,
		total_chunks: totalChunks,
		progress: totalChunks ? readyChunks / totalChunks : 0,
		ranges
	};
}

export function playbackReadinessAt(status: PlaybackReadinessStatus | null, timeMs: number) {
	if (!status) return null;
	return status.ranges.find((range) => timeMs >= range.start_ms && timeMs < range.end_ms) ?? null;
}

const PLAYBACK_STATUS_PRIORITY: Record<PlaybackReadinessRangeStatus, number> = {
	ready: 0,
	empty: 1,
	loading: 2,
	failed: 3
};

export function playbackRangesForViewport(
	ranges: PlaybackReadinessRange[],
	viewport: { startMs: number; endMs: number },
	maxSegments = 64
) {
	const visible = ranges.filter((range) =>
		range.end_ms > viewport.startMs && range.start_ms < viewport.endMs
	);
	const boundedMaximum = Math.max(1, Math.floor(maxSegments));
	if (visible.length <= boundedMaximum) return visible;
	const bucketSize = Math.ceil(visible.length / boundedMaximum);
	const projected: PlaybackReadinessRange[] = [];
	for (let index = 0; index < visible.length; index += bucketSize) {
		const bucket = visible.slice(index, index + bucketSize);
		const worst = bucket.reduce((current, range) =>
			PLAYBACK_STATUS_PRIORITY[range.status] > PLAYBACK_STATUS_PRIORITY[current.status]
				? range
				: current
		);
		projected.push({
			start_ms: bucket[0].start_ms,
			end_ms: bucket.at(-1)?.end_ms ?? bucket[0].end_ms,
			status: worst.status,
			blockers: [...new Set(bucket.flatMap((range) => range.blockers))]
		});
	}
	return projected;
}

const RANGE_EDGE_TOLERANCE_MS = 20;
const INTERNAL_GAP_TOLERANCE_MS = 2;

export function bufferedRangeCovered(ranges: PlaybackBufferedRange[], startMs: number, endMs: number) {
	if (endMs <= startMs) return true;
	let coveredUntil = startMs;
	for (const range of [...ranges].sort((left, right) => left.start_ms - right.start_ms)) {
		if (range.end_ms + RANGE_EDGE_TOLERANCE_MS < coveredUntil) continue;
		if (range.start_ms > coveredUntil + INTERNAL_GAP_TOLERANCE_MS) return false;
		coveredUntil = Math.max(coveredUntil, range.end_ms);
		if (coveredUntil + RANGE_EDGE_TOLERANCE_MS >= endMs) return true;
	}
	return false;
}

export function audioWindowReady(input: {
	targetMs: number;
	declaredEndMs: number;
	durationMs: number | null;
	buffered: PlaybackBufferedRange[];
	aheadMs: number;
	durationToleranceMs: number;
}) {
	const targetMs = Math.max(0, Math.round(input.targetMs));
	const declaredEndMs = Math.max(targetMs, Math.round(input.declaredEndMs));
	const durationMs = input.durationMs == null ? null : Math.max(0, Math.round(input.durationMs));
	if (durationMs != null && (
		targetMs > durationMs + input.durationToleranceMs
		|| declaredEndMs > durationMs + input.durationToleranceMs
	)) return false;
	const endMs = Math.min(
		declaredEndMs,
		targetMs + Math.max(0, input.aheadMs),
		durationMs ?? Number.POSITIVE_INFINITY
	);
	if (endMs <= targetMs + input.durationToleranceMs) return true;

	// Chromium can keep a complete buffered range while readyState falls back
	// to metadata for an inactive audio element. The concrete range is the
	// stronger readiness signal for this exact playback window.
	return bufferedRangeCovered(input.buffered, targetMs, endMs);
}
