export type TimelineViewState = {
	timeline_zoom?: number;
	timeline_viewport_start_ms?: number;
	playhead_ms?: number;
};

type SessionStorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

const KEY_PREFIX = 'voice-studio-video-localization-view:';

function finiteNonNegative(value: unknown) {
	const number = Number(value);
	return Number.isFinite(number) && number >= 0 ? number : undefined;
}

export function normalizeTimelineViewState(value: unknown): TimelineViewState {
	if (!value || typeof value !== 'object') return {};
	const source = value as Record<string, unknown>;
	const timelineZoom = finiteNonNegative(source.timeline_zoom);
	const timelineViewportStartMs = finiteNonNegative(source.timeline_viewport_start_ms);
	const playheadMs = finiteNonNegative(source.playhead_ms);
	return {
		...(timelineZoom !== undefined ? { timeline_zoom: Math.max(1, Math.min(1200, timelineZoom)) } : {}),
		...(timelineViewportStartMs !== undefined ? { timeline_viewport_start_ms: Math.round(timelineViewportStartMs) } : {}),
		...(playheadMs !== undefined ? { playhead_ms: Math.round(playheadMs) } : {})
	};
}

export function readTimelineViewState(storage: SessionStorageLike, projectId: string): TimelineViewState {
	if (!projectId) return {};
	try {
		return normalizeTimelineViewState(JSON.parse(storage.getItem(`${KEY_PREFIX}${projectId}`) ?? 'null'));
	} catch {
		storage.removeItem(`${KEY_PREFIX}${projectId}`);
		return {};
	}
}

export function resolveTimelineViewState(persisted: unknown, currentTab: unknown): TimelineViewState {
	return {
		...normalizeTimelineViewState(persisted),
		...normalizeTimelineViewState(currentTab)
	};
}

export function writeTimelineViewState(storage: SessionStorageLike, projectId: string, patch: TimelineViewState) {
	if (!projectId) return;
	const next = normalizeTimelineViewState({ ...readTimelineViewState(storage, projectId), ...patch });
	storage.setItem(`${KEY_PREFIX}${projectId}`, JSON.stringify(next));
}
