export type VideoLocalizationTrackId = 'original' | 'vocals' | 'background' | 'subtitles' | 'localizedSubtitles' | 'dub';

export const AUDIO_TRACK_IDS = ['original', 'vocals', 'dub', 'background'] as const;
export type VideoLocalizationAudioTrackId = (typeof AUDIO_TRACK_IDS)[number];
export type VideoLocalizationAudioTrackOrder = VideoLocalizationAudioTrackId[];

export type SubtitleCueTime = {
	cue_id: string;
	start_ms: number | null;
	end_ms: number | null;
};

export type VideoLocalizationTrackState = {
	muted: boolean;
	solo: boolean;
	volume: number;
	label?: string;
	locked?: boolean;
};

export type VideoLocalizationTrackStates = Record<VideoLocalizationTrackId, VideoLocalizationTrackState>;
export type VideoLocalizationDubLaneStates = Record<string, VideoLocalizationTrackState>;
export type VideoLocalizationMixTrackId = 'original' | 'vocals' | 'background';

export type VideoLocalizationAudibleMix = {
	hasSoloTrack: boolean;
	audibleTrackIds: VideoLocalizationMixTrackId[];
	audibleDubLaneIds: number[];
};

export const TRACK_LABELS: Record<VideoLocalizationTrackId, string> = {
	original: '原音轨',
	vocals: '人声轨',
	background: '背景音乐',
	subtitles: 'ASR 字幕',
	localizedSubtitles: '本土化字幕',
	dub: '合成配音'
};

const DEFAULT_TRACK_STATE: VideoLocalizationTrackState = {
	muted: false,
	solo: false,
	volume: 1
};

export function defaultTrackStates(): VideoLocalizationTrackStates {
	return {
		original: { ...DEFAULT_TRACK_STATE, solo: true },
		vocals: { ...DEFAULT_TRACK_STATE },
		background: { ...DEFAULT_TRACK_STATE },
		subtitles: { muted: false, solo: false, volume: 1 },
		localizedSubtitles: { muted: false, solo: false, volume: 1 },
		dub: { ...DEFAULT_TRACK_STATE }
	};
}

export function resolveAudibleMix({
	trackStates,
	trackMedia,
	dubLanes
}: {
	trackStates: VideoLocalizationTrackStates;
	trackMedia: Record<VideoLocalizationMixTrackId, boolean>;
	dubLanes: Array<{
		lane: number;
		hasMedia: boolean;
		state: VideoLocalizationTrackState;
	}>;
}): VideoLocalizationAudibleMix {
	const trackIds: VideoLocalizationMixTrackId[] = ['original', 'vocals', 'background'];
	const soloTrackIds = trackIds.filter((trackId) => trackMedia[trackId] && trackStates[trackId].solo);
	const soloDubLaneIds = dubLanes
		.filter(({ hasMedia, state }) => hasMedia && state.solo)
		.map(({ lane }) => lane);
	const hasSoloTrack = soloTrackIds.length > 0 || soloDubLaneIds.length > 0;
	return {
		hasSoloTrack,
		audibleTrackIds: trackIds.filter((trackId) => {
			const state = trackStates[trackId];
			return trackMedia[trackId]
				&& !state.muted
				&& state.volume > 0
				&& (!hasSoloTrack || soloTrackIds.includes(trackId));
		}),
		audibleDubLaneIds: dubLanes
			.filter(({ lane, hasMedia, state }) =>
				hasMedia
				&& !state.muted
				&& state.volume > 0
				&& (!hasSoloTrack || soloDubLaneIds.includes(lane))
			)
			.map(({ lane }) => lane)
	};
}

export function defaultAudioTrackOrder(): VideoLocalizationAudioTrackOrder {
	return [...AUDIO_TRACK_IDS];
}

export function resolveAudioTrackOrder(value: unknown): VideoLocalizationAudioTrackOrder {
	const valid = new Set<string>(AUDIO_TRACK_IDS);
	const resolved: VideoLocalizationAudioTrackId[] = [];
	if (Array.isArray(value)) {
		for (const item of value) {
			if (typeof item !== 'string' || !valid.has(item) || resolved.includes(item as VideoLocalizationAudioTrackId)) continue;
			resolved.push(item as VideoLocalizationAudioTrackId);
		}
	}
	for (const trackId of AUDIO_TRACK_IDS) {
		if (!resolved.includes(trackId)) resolved.push(trackId);
	}
	return resolved;
}

export function reorderAudioTracks(
	order: VideoLocalizationAudioTrackOrder,
	draggedId: VideoLocalizationAudioTrackId,
	targetId: VideoLocalizationAudioTrackId,
	placement: 'before' | 'after' = 'before'
): VideoLocalizationAudioTrackOrder {
	const next = resolveAudioTrackOrder(order).filter((trackId) => trackId !== draggedId);
	const targetIndex = next.indexOf(targetId);
	if (targetIndex < 0) return resolveAudioTrackOrder(order);
	next.splice(targetIndex + (placement === 'after' ? 1 : 0), 0, draggedId);
	return resolveAudioTrackOrder(next);
}

export function subtitleCueDragBounds(cues: SubtitleCueTime[], cueId: string, timelineDurationMs: number) {
	const sorted = cues
		.filter((cue) => cue.start_ms !== null && cue.end_ms !== null)
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0));
	const index = sorted.findIndex((cue) => cue.cue_id === cueId);
	if (index < 0) return { minStartMs: 0, maxEndMs: Math.max(0, timelineDurationMs) };
	const previous = sorted[index - 1];
	const next = sorted[index + 1];
	return {
		minStartMs: Math.max(0, previous?.end_ms ?? 0),
		maxEndMs: Math.max(0, Math.min(timelineDurationMs, next?.start_ms ?? timelineDurationMs))
	};
}

export const TIMELINE_FRAME_RATE = 30;
export const MIN_SUBTITLE_DURATION_MS = Math.ceil(1000 / TIMELINE_FRAME_RATE);

export function extendSubtitleCuesToFollowingStart<T extends { start_ms: number | null; end_ms: number | null }>(
	cues: T[]
): T[] {
	const next = cues.map((cue) => ({ ...cue }));
	const ordered = next
		.map((cue, index) => ({ cue, index }))
		.filter(({ cue }) => cue.start_ms !== null && cue.end_ms !== null)
		.sort((left, right) => (left.cue.start_ms ?? 0) - (right.cue.start_ms ?? 0));
	for (let index = 0; index < ordered.length - 1; index += 1) {
		const current = ordered[index].cue;
		const following = ordered[index + 1].cue;
		const currentEnd = current.end_ms ?? 0;
		const followingStart = following.start_ms ?? currentEnd;
		const gapMs = followingStart - currentEnd;
		if (gapMs > 0) current.end_ms = followingStart;
	}
	return next;
}

export function timelineViewportRange(
	timelineDurationMs: number,
	timelineZoom: number,
	scrollLeft: number,
	viewportWidth: number,
	overscanViewports = 0.5
) {
	const safeDuration = Math.max(0, timelineDurationMs);
	const safeViewport = Math.max(1, viewportWidth || 1);
	const contentWidth = Math.max(safeViewport, safeViewport * Math.max(1, timelineZoom));
	const viewportDuration = safeDuration * (safeViewport / contentWidth);
	const startMs = safeDuration * (Math.max(0, scrollLeft) / contentWidth);
	const overscanMs = viewportDuration * Math.max(0, overscanViewports);
	return {
		startMs: Math.max(0, startMs - overscanMs),
		endMs: Math.min(safeDuration, startMs + viewportDuration + overscanMs)
	};
}

export function timeRangeIntersectsViewport(
	startMs: number | null | undefined,
	endMs: number | null | undefined,
	viewport: { startMs: number; endMs: number }
) {
	if (startMs === null || startMs === undefined || endMs === null || endMs === undefined) return false;
	return endMs >= viewport.startMs && startMs <= viewport.endMs;
}

export function resolveTrackStates(value: unknown): VideoLocalizationTrackStates {
	const defaults = defaultTrackStates();
	if (!value || typeof value !== 'object') return defaults;
	const raw = value as Record<string, Partial<VideoLocalizationTrackState>>;
	for (const key of Object.keys(defaults) as VideoLocalizationTrackId[]) {
		const track = raw[key];
		if (!track) continue;
		const solo = track.solo === true;
		defaults[key] = {
			muted: solo ? false : track.muted === true,
			solo,
			volume: clampNumber(track.volume, 0, 4, 1),
			label: typeof track.label === 'string' ? track.label : undefined,
			locked: track.locked === true
		};
	}
	return defaults;
}

export function resolveDubLaneStates(
	value: unknown,
	laneCount: number,
	legacyDubState: VideoLocalizationTrackState
): VideoLocalizationDubLaneStates {
	const raw = value && typeof value === 'object'
		? value as Record<string, Partial<VideoLocalizationTrackState>>
		: {};
	const visibleLaneCount = Math.max(1, laneCount);
	// A secondary dub lane only exists while it owns at least one clip. Stale
	// control state must not resurrect an empty lane after its last clip moved.
	const laneKeys = new Set<string>();
	for (let lane = 0; lane < visibleLaneCount; lane += 1) laneKeys.add(String(lane));
	const states: VideoLocalizationDubLaneStates = {};
	for (const key of [...laneKeys].sort((left, right) => Number(left) - Number(right))) {
		const lane = Number(key);
		const fallback = lane === 0 ? legacyDubState : DEFAULT_TRACK_STATE;
		const track = raw[key] ?? {};
		const solo = (track.solo ?? fallback.solo) === true;
		states[key] = {
			muted: solo ? false : (track.muted ?? fallback.muted) === true,
			solo,
			volume: clampNumber(track.volume, 0, 4, fallback.volume),
			label: typeof track.label === 'string'
				? track.label
				: lane === 0
					? fallback.label
					: undefined,
			locked: (track.locked ?? fallback.locked) === true
		};
	}
	return states;
}

export function clampNumber(value: unknown, min: number, max: number, fallback: number) {
	const parsed = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(parsed)) return fallback;
	return Math.max(min, Math.min(max, parsed));
}
