import { lastFrameStartMs, normalizeFrameRate } from './frame-timeline';

type TimelineRange = {
	start_ms?: number | null;
	end_ms?: number | null;
};

export type TimelineNavigationSources = {
	durationMs: number;
	frameRate?: number | null;
	asrCues: TimelineRange[];
	localizedSubtitles: TimelineRange[];
	audioClips: TimelineRange[];
};

export function timelineEditPoints(sources: TimelineNavigationSources) {
	const durationMs = Math.max(0, Math.round(sources.durationMs));
	const lastPlayableMs = durationMs > 0
		? lastFrameStartMs(durationMs, normalizeFrameRate(sources.frameRate))
		: 0;
	const points = new Set<number>();
	for (const item of [...sources.asrCues, ...sources.localizedSubtitles, ...sources.audioClips]) {
		for (const value of [item.start_ms, item.end_ms]) {
			if (typeof value !== 'number' || !Number.isFinite(value)) continue;
			const rawPoint = Math.round(value);
			if (rawPoint < 0 || (durationMs > 0 && rawPoint > durationMs)) continue;
			const point = durationMs > 0 ? Math.min(rawPoint, lastPlayableMs) : rawPoint;
			points.add(point);
		}
	}
	return [...points].sort((left, right) => left - right);
}

export function timelineBoundaryTarget(
	sources: TimelineNavigationSources,
	currentTimeMs: number,
	direction: 'previous' | 'next'
) {
	const points = timelineEditPoints(sources);
	const current = Math.max(0, Math.round(currentTimeMs));
	if (direction === 'previous') {
		for (let index = points.length - 1; index >= 0; index -= 1) {
			if (points[index] < current - 1) return points[index];
		}
		return 0;
	}
	const next = points.find((point) => point > current + 1);
	if (next !== undefined) return next;
	return lastFrameStartMs(Math.max(0, sources.durationMs), normalizeFrameRate(sources.frameRate));
}
