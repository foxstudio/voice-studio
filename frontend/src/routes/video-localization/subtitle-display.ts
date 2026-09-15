import type {
	VideoLocalizationCue,
	VideoLocalizationDubSubtitleCue,
	VideoLocalizationOperation,
	VideoLocalizationSubtitleCue
} from '$lib/api/types';
import type { AsrOperationPreview, AsrPreviewCue } from './asr-operation-preview';

export type SubtitleDisplaySource = 'asr' | 'localized';
export type SubtitleStylePreset =
	| 'yellow-outline'
	| 'boxed'
	| 'clean-shadow'
	| 'strong-outline'
	| 'warm-outline'
	| 'cyan-outline'
	| 'caption-bar'
	| 'soft-panel';
export type SubtitlePosition = 'bottom' | 'middle';

export type SubtitleDisplayStyle = {
	stylePreset: SubtitleStylePreset;
	fontSize: number;
	backgroundOpacity: number;
	offsetX: number;
	offsetY: number;
};

export type SubtitleDisplaySettings = {
	position: SubtitlePosition;
	sources: Record<SubtitleDisplaySource, boolean>;
	trackStyles: Record<SubtitleDisplaySource, SubtitleDisplayStyle>;
};

export type SubtitleDisplayTrackAvailability = Record<SubtitleDisplaySource, boolean>;

export type SubtitleDisplaySelection = {
	source: SubtitleDisplaySource;
	id: string;
};

export type SubtitleDisplayCue = {
	key: string;
	id: string;
	source: SubtitleDisplaySource;
	start_ms: number;
	end_ms: number;
	text: string;
	provisional: boolean;
	editable: boolean;
	reviewable: boolean;
	phaseLabel: string | null;
	raw: VideoLocalizationCue | VideoLocalizationSubtitleCue | VideoLocalizationDubSubtitleCue | AsrPreviewCue;
};

export type SubtitleDisplayTrack = {
	source: SubtitleDisplaySource;
	cues: SubtitleDisplayCue[];
	visible: boolean;
	provisional: boolean;
	phaseLabel: string | null;
	isActive: boolean;
};

export type SubtitleDisplayLine = {
	key: string;
	source: SubtitleDisplaySource;
	text: string;
	cue: SubtitleDisplayCue | null;
	style: SubtitleDisplayStyle;
	placeholder: boolean;
};

export type SubtitleDisplayInput = {
	settings: SubtitleDisplaySettings;
	asrCues?: VideoLocalizationCue[];
	asrPreview?: AsrOperationPreview | null;
	localizedCues?: VideoLocalizationSubtitleCue[];
	localizedPreview?: VideoLocalizationSubtitleCue[];
	localizedPreviewLabel?: string | null;
	localizedPreviewActive?: boolean;
	localizedAlternative?: {
		cues: VideoLocalizationDubSubtitleCue[];
		label: string;
	} | null;
	selection?: SubtitleDisplaySelection | null;
};

export type SubtitleDisplayFrame = {
	timeMs: number;
	position: SubtitlePosition;
	currentCues: Record<SubtitleDisplaySource, SubtitleDisplayCue | null>;
	lines: SubtitleDisplayLine[];
};

export const SUBTITLE_STYLE_LABELS: Record<SubtitleStylePreset, string> = {
	'yellow-outline': '黄字黑描边',
	boxed: '半透明黑底',
	'clean-shadow': '白字轻阴影',
	'strong-outline': '白字强描边',
	'warm-outline': '暖白细描边',
	'cyan-outline': '青白冷描边',
	'caption-bar': '电影字幕条',
	'soft-panel': '柔光暗底'
};

export class SubtitleDisplayModel {
	readonly settings: SubtitleDisplaySettings;
	readonly tracks: Record<SubtitleDisplaySource, SubtitleDisplayTrack>;
	readonly activeSource: SubtitleDisplaySource;
	readonly selectedCue: SubtitleDisplayCue | null;

	constructor(input: SubtitleDisplayInput) {
		this.settings = input.settings;
		const asr = this.buildAsrTrack(input);
		const localized = this.buildLocalizedTrack(input);
		this.tracks = { asr, localized };
		this.selectedCue = input.selection
			? this.tracks[input.selection.source].cues.find((cue) => cue.id === input.selection?.id) ?? null
			: null;
		this.activeSource = this.selectedCue?.source
			?? input.selection?.source
			?? (localized.visible && localized.cues.length
				? 'localized'
				: asr.visible && asr.cues.length
					? 'asr'
					: localized.visible
						? 'localized'
						: 'asr');
	}

	static resolveSettings(
		value: unknown,
		availability?: SubtitleDisplayTrackAvailability
	): SubtitleDisplaySettings {
		return resolveSubtitleDisplaySettings(value, availability);
	}

	static resolveLocalizedPreview(
		operations: VideoLocalizationOperation[]
	): VideoLocalizationSubtitleCue[] {
		return resolveLocalizedOperationPreview(
			operations,
			'localization_draft',
			'localized_preview'
		);
	}

	static resolveDubSubtitlePreview(
		operations: VideoLocalizationOperation[]
	): VideoLocalizationSubtitleCue[] {
		return resolveLocalizedOperationPreview(
			operations,
			'dub_subtitle_generation',
			'dub_subtitle_preview'
		);
	}

	track(source: SubtitleDisplaySource): SubtitleDisplayTrack {
		return this.tracks[source];
	}

	toggleSource(source: SubtitleDisplaySource): SubtitleDisplaySettings {
		return {
			...this.settings,
			sources: {
				...this.settings.sources,
				[source]: !this.settings.sources[source]
			}
		};
	}

	updateStyle(
		source: SubtitleDisplaySource,
		patch: Partial<SubtitleDisplayStyle>
	): SubtitleDisplaySettings {
		return {
			...this.settings,
			trackStyles: {
				...this.settings.trackStyles,
				[source]: resolveSubtitleDisplayStyle(
					{ ...this.settings.trackStyles[source], ...patch },
					this.settings.trackStyles[source]
				)
			}
		};
	}

	frameAt(timeMs: number): SubtitleDisplayFrame {
		const currentCues = {
			asr: cueAtTime(this.tracks.asr.cues, timeMs),
			localized: cueAtTime(this.tracks.localized.cues, timeMs)
		};
		return {
			timeMs,
			position: this.settings.position,
			currentCues,
			lines: this.buildLines(timeMs)
		};
	}

	private buildAsrTrack(input: SubtitleDisplayInput): SubtitleDisplayTrack {
		const provisional = Boolean(input.asrPreview?.cues.length);
		const cues = provisional
			? (input.asrPreview?.cues ?? []).map((cue) => normalizeAsrPreviewCue(cue, input.asrPreview?.phaseLabel ?? null))
			: (input.asrCues ?? []).flatMap(normalizeCommittedAsrCue);
		return buildTrack({
			source: 'asr',
			cues,
			visible: input.settings.sources.asr,
			provisional,
			phaseLabel: provisional ? input.asrPreview?.phaseLabel ?? null : null,
			isActive: provisional && input.asrPreview?.isActive === true
		});
	}

	private buildLocalizedTrack(input: SubtitleDisplayInput): SubtitleDisplayTrack {
		const provisional = Boolean(input.localizedPreview?.length);
		const phaseLabel = provisional
			? input.localizedPreviewLabel?.trim() || '本土化字幕生成中'
			: input.localizedAlternative?.label.trim() || null;
		const cues = provisional
			? (input.localizedPreview ?? []).flatMap((cue) => normalizeLocalizedCue(cue, true, phaseLabel))
			: input.localizedAlternative
				? input.localizedAlternative.cues.flatMap((cue) => normalizeDubSubtitleCue(cue, phaseLabel))
				: (input.localizedCues ?? []).flatMap((cue) => normalizeLocalizedCue(cue, false, null));
		return buildTrack({
			source: 'localized',
			cues,
			visible: input.settings.sources.localized,
			provisional,
			phaseLabel,
			isActive: provisional && input.localizedPreviewActive !== false
		});
	}

	private buildLines(
		timeMs: number
	): SubtitleDisplayLine[] {
		const visibleSources = (['localized', 'asr'] as const)
			.filter((source) => this.tracks[source].visible);
		const activeCues = Object.fromEntries(
			visibleSources.map((source) => [
				source,
				cuesAtTime(this.tracks[source].cues, timeMs)
					.filter((cue) => cue.text.trim())
			])
		) as Record<SubtitleDisplaySource, SubtitleDisplayCue[]>;
		const hasVisibleText = visibleSources.some(
			(source) => activeCues[source].length > 0
		);
		if (!hasVisibleText) return [];
		return visibleSources.flatMap((source): SubtitleDisplayLine[] => {
			const cues = activeCues[source];
			if (!cues.length) {
				return [{
					key: `subtitle-slot:${source}`,
					source,
					text: '\u00a0',
					cue: null,
					style: this.settings.trackStyles[source],
					placeholder: true
				}];
			}
			return cues.map((cue) => ({
				key: `subtitle-slot:${source}:${cue.key}`,
				source,
				text: cue.text,
				cue,
				style: this.settings.trackStyles[source],
				placeholder: false
			}));
		});
	}
}

function resolveLocalizedOperationPreview(
	operations: VideoLocalizationOperation[],
	kind: 'localization_draft' | 'dub_subtitle_generation',
	fallbackIdPrefix: string
): VideoLocalizationSubtitleCue[] {
	const operation = operations.find((item) =>
		item.kind === kind
		&& (
			kind !== 'dub_subtitle_generation'
			|| item.result_summary?.preview_phase === 'timing_segmentation'
		)
		&& (
			item.status === 'queued'
			|| item.status === 'running'
			|| (
				item.status === 'success'
				&& item.parameters?.execution_mode === 'development_target'
				&& item.result_summary?.stage_id !== 'commit'
			)
		)
	);
	const raw = operation?.result_summary?.preview_cues;
	if (!Array.isArray(raw)) return [];
	return raw.flatMap((item, index) => {
		if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
		const cue = item as Record<string, unknown>;
		const startMs = Number(cue.start_ms);
		const endMs = Number(cue.end_ms);
		const text = typeof cue.text === 'string' ? cue.text.trim() : '';
		if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !text) return [];
		return [{
			subtitle_id: typeof cue.subtitle_id === 'string'
				? cue.subtitle_id
				: `${fallbackIdPrefix}_${index + 1}`,
			start_ms: Math.max(0, Math.round(startMs)),
			end_ms: Math.max(1, Math.round(endMs)),
			text,
			tts_text: typeof cue.tts_text === 'string' ? cue.tts_text : null,
			linked_cue_id: null,
			quality_flags: Array.isArray(cue.quality_flags) ? cue.quality_flags.map(String) : []
		}];
	});
}

export function defaultSubtitleDisplaySettings(): SubtitleDisplaySettings {
	const defaultStyle = defaultSubtitleDisplayStyle();
	return {
		position: 'bottom',
		sources: {
			asr: true,
			localized: true
		},
		trackStyles: {
			asr: { ...defaultStyle },
			localized: { ...defaultStyle }
		}
	};
}

export function defaultSubtitleDisplayStyle(): SubtitleDisplayStyle {
	return {
		stylePreset: 'yellow-outline',
		fontSize: 18,
		backgroundOpacity: 0,
		offsetX: 0,
		offsetY: 0
	};
}

export function resolveSubtitleDisplaySettings(
	value: unknown,
	availability?: SubtitleDisplayTrackAvailability
): SubtitleDisplaySettings {
	const defaults = defaultSubtitleDisplaySettings();
	if (!value || typeof value !== 'object') return defaults;
	const raw = value as Record<string, unknown>;
	const legacyStyle = resolveSubtitleDisplayStyle(raw, defaultSubtitleDisplayStyle());
	const rawTrackStyles = raw.trackStyles && typeof raw.trackStyles === 'object'
		? raw.trackStyles as Partial<Record<SubtitleDisplaySource, unknown>>
		: null;
	const explicitSources = resolveSubtitleDisplaySources(raw.sources);
	const legacySources = explicitSources
		? null
		: resolveLegacySubtitleDisplaySources(raw.source, availability);
	return {
		position: raw.position === 'middle' ? 'middle' : 'bottom',
		sources: raw.enabled === false
			? { asr: false, localized: false }
			: explicitSources ?? legacySources ?? defaults.sources,
		trackStyles: {
			asr: resolveSubtitleDisplayStyle(rawTrackStyles?.asr, legacyStyle),
			localized: resolveSubtitleDisplayStyle(rawTrackStyles?.localized, legacyStyle)
		}
	};
}

export function resolveSubtitleDisplayStyle(
	value: unknown,
	fallback = defaultSubtitleDisplayStyle()
): SubtitleDisplayStyle {
	if (!value || typeof value !== 'object') return { ...fallback };
	const raw = value as Partial<SubtitleDisplayStyle>;
	return {
		stylePreset: isSubtitleStylePreset(raw.stylePreset) ? raw.stylePreset : fallback.stylePreset,
		fontSize: clampNumber(raw.fontSize, 12, 32, fallback.fontSize),
		backgroundOpacity: clampNumber(raw.backgroundOpacity, 0, 0.8, fallback.backgroundOpacity),
		offsetX: clampNumber(raw.offsetX, -240, 240, fallback.offsetX),
		offsetY: clampNumber(raw.offsetY, -160, 160, fallback.offsetY)
	};
}

function buildTrack(input: SubtitleDisplayTrack): SubtitleDisplayTrack {
	return {
		...input,
		cues: [...input.cues].sort((left, right) =>
			left.start_ms - right.start_ms
			|| left.end_ms - right.end_ms
			|| dubLane(left) - dubLane(right)
			|| left.id.localeCompare(right.id)
		),
		visible: input.visible,
		provisional: input.provisional,
		phaseLabel: input.phaseLabel,
		isActive: input.isActive
	};
}

function normalizeAsrPreviewCue(cue: AsrPreviewCue, phaseLabel: string | null): SubtitleDisplayCue {
	return {
		key: `asr:preview:${cue.cue_id}`,
		id: cue.cue_id,
		source: 'asr',
		start_ms: cue.start_ms,
		end_ms: cue.end_ms,
		text: cue.text.trim(),
		provisional: true,
		editable: false,
		reviewable: false,
		phaseLabel,
		raw: cue
	};
}

function normalizeCommittedAsrCue(cue: VideoLocalizationCue): SubtitleDisplayCue[] {
	const startMs = Number(cue.start_ms);
	const endMs = Number(cue.end_ms);
	const text = cue.en_subtitle_text?.trim() ?? '';
	if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return [];
	return [{
		key: `asr:persisted:${cue.cue_id}`,
		id: cue.cue_id,
		source: 'asr',
		start_ms: Math.max(0, Math.round(startMs)),
		end_ms: Math.max(1, Math.round(endMs)),
		text,
		provisional: false,
		editable: true,
		reviewable: false,
		phaseLabel: null,
		raw: cue
	}];
}

function normalizeLocalizedCue(
	cue: VideoLocalizationSubtitleCue,
	provisional: boolean,
	phaseLabel: string | null
): SubtitleDisplayCue[] {
	const startMs = Number(cue.start_ms);
	const endMs = Number(cue.end_ms);
	const text = cue.text.trim();
	if (
		!Number.isFinite(startMs)
		|| !Number.isFinite(endMs)
		|| endMs <= startMs
		|| (provisional && !text)
	) return [];
	return [{
		key: `localized:${provisional ? 'preview' : 'persisted'}:${cue.subtitle_id}`,
		id: cue.subtitle_id,
		source: 'localized',
		start_ms: Math.max(0, Math.round(startMs)),
		end_ms: Math.max(1, Math.round(endMs)),
		text,
		provisional,
		editable: !provisional,
		reviewable: false,
		phaseLabel: provisional ? phaseLabel : null,
		raw: cue
	}];
}

function normalizeDubSubtitleCue(
	cue: VideoLocalizationDubSubtitleCue,
	phaseLabel: string | null
): SubtitleDisplayCue[] {
	const startMs = Number(cue.start_ms);
	const endMs = Number(cue.end_ms);
	const text = cue.text.trim();
	if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return [];
	return [{
		key: `localized:dub:${cue.subtitle_id}`,
		id: cue.subtitle_id,
		source: 'localized',
		start_ms: Math.max(0, Math.round(startMs)),
		end_ms: Math.max(1, Math.round(endMs)),
		text,
		provisional: false,
		editable: false,
		reviewable: true,
		phaseLabel,
		raw: cue
	}];
}

function cueAtTime(cues: SubtitleDisplayCue[], timeMs: number): SubtitleDisplayCue | null {
	if (!Number.isFinite(timeMs)) return null;
	return cues.find((cue) => timeMs >= cue.start_ms && timeMs < cue.end_ms) ?? null;
}

function cuesAtTime(cues: SubtitleDisplayCue[], timeMs: number): SubtitleDisplayCue[] {
	if (!Number.isFinite(timeMs)) return [];
	return cues.filter((cue) => timeMs >= cue.start_ms && timeMs < cue.end_ms);
}

function dubLane(cue: SubtitleDisplayCue): number {
	const raw = cue.raw as Partial<VideoLocalizationDubSubtitleCue>;
	return Array.isArray(raw.dub_lanes)
		? Number(raw.dub_lanes[0] ?? 0)
		: 0;
}

function resolveSubtitleDisplaySources(value: unknown): Record<SubtitleDisplaySource, boolean> | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Partial<Record<SubtitleDisplaySource, unknown>>;
	return {
		asr: raw.asr === true,
		localized: raw.localized === true
	};
}

function resolveLegacySubtitleDisplaySources(
	value: unknown,
	availability?: SubtitleDisplayTrackAvailability
): Record<SubtitleDisplaySource, boolean> | null {
	if (value !== 'asr' && value !== 'localized') return null;
	const fallback: SubtitleDisplaySource = value === 'asr' ? 'localized' : 'asr';
	const source = availability && !availability[value] && availability[fallback]
		? fallback
		: value;
	return {
		asr: source === 'asr',
		localized: source === 'localized'
	};
}

function isSubtitleStylePreset(value: unknown): value is SubtitleStylePreset {
	return value === 'yellow-outline'
		|| value === 'boxed'
		|| value === 'clean-shadow'
		|| value === 'strong-outline'
		|| value === 'warm-outline'
		|| value === 'cyan-outline'
		|| value === 'caption-bar'
		|| value === 'soft-panel';
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
	const parsed = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(parsed)) return fallback;
	return Math.max(min, Math.min(max, parsed));
}
