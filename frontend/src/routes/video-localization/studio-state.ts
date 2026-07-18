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

export type SubtitlePreviewSource = 'asr' | 'localized';
export type SubtitleStylePreset = 'yellow-outline' | 'boxed' | 'clean-shadow' | 'strong-outline';
export type SubtitlePosition = 'bottom' | 'middle';
export type SubtitlePreviewSources = {
	asr: boolean;
	localized: boolean;
};

export type SubtitlePreviewState = {
	enabled: boolean;
	source: SubtitlePreviewSource;
	stylePreset: SubtitleStylePreset;
	fontSize: number;
	backgroundOpacity: number;
	position: SubtitlePosition;
	sources?: SubtitlePreviewSources | null;
};

export const TRACK_LABELS: Record<VideoLocalizationTrackId, string> = {
	original: '原音轨',
	vocals: '人声轨',
	background: '背景音乐',
	subtitles: 'ASR 字幕',
	localizedSubtitles: '本土化字幕',
	dub: '合成配音'
};

export const SUBTITLE_SOURCE_LABELS: Record<SubtitlePreviewSource, string> = {
	asr: 'ASR 字幕',
	localized: '本土化字幕'
};

export const SUBTITLE_STYLE_LABELS: Record<SubtitleStylePreset, string> = {
	'yellow-outline': '黄字黑描边',
	boxed: '半透明黑底',
	'clean-shadow': '白字轻阴影',
	'strong-outline': '白字强描边'
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

export function extendSubtitleCuesAcrossShortGaps<T extends { start_ms: number | null; end_ms: number | null }>(
	cues: T[],
	maxGapMs = 320
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
		if (gapMs > 0 && gapMs <= maxGapMs) current.end_ms = followingStart;
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

export function defaultSubtitlePreviewState(): SubtitlePreviewState {
	return {
		enabled: true,
		source: 'localized',
		stylePreset: 'yellow-outline',
		fontSize: 18,
		backgroundOpacity: 0,
		position: 'bottom',
		sources: null
	};
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

export function resolveSubtitlePreviewState(value: unknown): SubtitlePreviewState {
	const defaults = defaultSubtitlePreviewState();
	if (!value || typeof value !== 'object') return defaults;
	const raw = value as Partial<SubtitlePreviewState>;
	return {
		enabled: raw.enabled !== false,
		source: isSubtitleSource(raw.source) ? raw.source : defaults.source,
		stylePreset: isSubtitleStylePreset(raw.stylePreset) ? raw.stylePreset : defaults.stylePreset,
		fontSize: clampNumber(raw.fontSize, 12, 32, defaults.fontSize),
		backgroundOpacity: clampNumber(raw.backgroundOpacity, 0, 0.8, defaults.backgroundOpacity),
		position: raw.position === 'middle' ? 'middle' : 'bottom',
		sources: resolveSubtitlePreviewSources(raw.sources)
	};
}

export function clampNumber(value: unknown, min: number, max: number, fallback: number) {
	const parsed = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(parsed)) return fallback;
	return Math.max(min, Math.min(max, parsed));
}

function isSubtitleSource(value: unknown): value is SubtitlePreviewSource {
	return value === 'asr' || value === 'localized';
}

function resolveSubtitlePreviewSources(value: unknown): SubtitlePreviewSources | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Partial<SubtitlePreviewSources>;
	return {
		asr: raw.asr === true,
		localized: raw.localized === true
	};
}

function isSubtitleStylePreset(value: unknown): value is SubtitleStylePreset {
	return value === 'yellow-outline' || value === 'boxed' || value === 'clean-shadow' || value === 'strong-outline';
}
