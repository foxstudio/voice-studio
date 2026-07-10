export type VideoLocalizationTrackId = 'original' | 'vocals' | 'background' | 'subtitles' | 'dub';

export type VideoLocalizationTrackState = {
	muted: boolean;
	solo: boolean;
	volume: number;
	label?: string;
	collapsed?: boolean;
};

export type VideoLocalizationTrackStates = Record<VideoLocalizationTrackId, VideoLocalizationTrackState>;

export type SubtitlePreviewSource = 'auto' | 'asr' | 'localized' | 'tts' | 'compare';
export type SubtitleStylePreset = 'yellow-outline' | 'boxed' | 'clean-shadow' | 'strong-outline';
export type SubtitlePosition = 'bottom' | 'middle';
export type SubtitlePreviewSources = {
	asr: boolean;
	localized: boolean;
	tts: boolean;
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
	subtitles: '字幕轨',
	dub: '中文配音'
};

export const SUBTITLE_SOURCE_LABELS: Record<SubtitlePreviewSource, string> = {
	auto: '自动',
	asr: '原文/ASR',
	localized: '本土化',
	tts: 'TTS',
	compare: '多行对照'
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
		original: { ...DEFAULT_TRACK_STATE },
		vocals: { ...DEFAULT_TRACK_STATE },
		background: { ...DEFAULT_TRACK_STATE },
		subtitles: { muted: false, solo: false, volume: 1 },
		dub: { ...DEFAULT_TRACK_STATE }
	};
}

export function defaultSubtitlePreviewState(): SubtitlePreviewState {
	return {
		enabled: true,
		source: 'auto',
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
		const track = raw[key] ?? {};
		const solo = track.solo === true;
		defaults[key] = {
			muted: solo ? false : track.muted === true,
			solo,
			volume: clampNumber(track.volume, 0, 1, 1),
			label: typeof track.label === 'string' ? track.label : undefined,
			collapsed: track.collapsed === true
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
	return value === 'auto' || value === 'asr' || value === 'localized' || value === 'tts' || value === 'compare';
}

function resolveSubtitlePreviewSources(value: unknown): SubtitlePreviewSources | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Partial<SubtitlePreviewSources>;
	return {
		asr: raw.asr === true,
		localized: raw.localized === true,
		tts: raw.tts === true
	};
}

function isSubtitleStylePreset(value: unknown): value is SubtitleStylePreset {
	return value === 'yellow-outline' || value === 'boxed' || value === 'clean-shadow' || value === 'strong-outline';
}
