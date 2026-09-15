import type { VideoLocalizationCue, VideoLocalizationSubtitleCue } from '$lib/api/types';
import type { TimelineSelectionItem } from './timeline-context-menu';

export type TtsSelectionAnchor = {
	kind: 'source' | 'target';
	itemId: string;
};

export type TtsSelectionSession = {
	anchor: TtsSelectionAnchor | null;
	sourceCueIds: string[];
	localizedSubtitleIds: string[];
	mappedSourceCueIds: string[];
	mappedLocalizedSubtitleIds: string[];
	explicitSourceCueIds: string[];
	explicitLocalizedSubtitleIds: string[];
	excludedSourceCueIds: string[];
	excludedLocalizedSubtitleIds: string[];
};

export const EMPTY_TTS_SELECTION_SESSION: TtsSelectionSession = {
	anchor: null,
	sourceCueIds: [],
	localizedSubtitleIds: [],
	mappedSourceCueIds: [],
	mappedLocalizedSubtitleIds: [],
	explicitSourceCueIds: [],
	explicitLocalizedSubtitleIds: [],
	excludedSourceCueIds: [],
	excludedLocalizedSubtitleIds: []
};

function unique(values: readonly string[]) {
	return Array.from(new Set(values.filter(Boolean)));
}

function subtitleSourceCueIds(subtitle: VideoLocalizationSubtitleCue) {
	return subtitle.source_cue_ids?.length
		? unique(subtitle.source_cue_ids)
		: unique([subtitle.linked_cue_id ?? '']);
}

function materializeSession(input: {
	anchor: TtsSelectionAnchor | null;
	explicitSourceCueIds: string[];
	explicitLocalizedSubtitleIds: string[];
	excludedSourceCueIds?: string[];
	excludedLocalizedSubtitleIds?: string[];
	cues: readonly VideoLocalizationCue[];
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[];
}): TtsSelectionSession {
	if (!input.anchor) return { ...EMPTY_TTS_SELECTION_SESSION };
	const validCueIds = new Set(input.cues.map((cue) => cue.cue_id));
	const validSubtitleIds = new Set(input.localizedSubtitles.map((subtitle) => subtitle.subtitle_id));
	const explicitSourceCueIds = unique(input.explicitSourceCueIds).filter((id) => validCueIds.has(id));
	const explicitLocalizedSubtitleIds = unique(input.explicitLocalizedSubtitleIds).filter((id) => validSubtitleIds.has(id));
	const excludedSourceCueIds = unique(input.excludedSourceCueIds ?? []).filter((id) => validCueIds.has(id));
	const excludedLocalizedSubtitleIds = unique(input.excludedLocalizedSubtitleIds ?? []).filter((id) => validSubtitleIds.has(id));

	const sourceSet = new Set(explicitSourceCueIds);
	const targetSet = new Set(explicitLocalizedSubtitleIds);
	const derivedTargetIds = input.localizedSubtitles
		.filter((subtitle) => subtitleSourceCueIds(subtitle).some((cueId) => sourceSet.has(cueId)))
		.map((subtitle) => subtitle.subtitle_id);
	const derivedSourceIds = unique(input.localizedSubtitles
		.filter((subtitle) => targetSet.has(subtitle.subtitle_id))
		.flatMap(subtitleSourceCueIds));
	const sourceCandidates = input.cues
		.map((cue) => cue.cue_id)
		.filter((id) => explicitSourceCueIds.includes(id) || derivedSourceIds.includes(id));
	const targetCandidates = input.localizedSubtitles
		.map((subtitle) => subtitle.subtitle_id)
		.filter((id) => explicitLocalizedSubtitleIds.includes(id) || derivedTargetIds.includes(id));
	return {
		anchor: input.anchor.kind === 'source'
			? (validCueIds.has(input.anchor.itemId) ? input.anchor : null)
			: (validSubtitleIds.has(input.anchor.itemId) ? input.anchor : null),
		sourceCueIds: explicitSourceCueIds.length
			? explicitSourceCueIds
			: sourceCandidates.filter((id) => !excludedSourceCueIds.includes(id)),
		localizedSubtitleIds: explicitLocalizedSubtitleIds.length
			? explicitLocalizedSubtitleIds
			: targetCandidates.filter((id) => !excludedLocalizedSubtitleIds.includes(id)),
		mappedSourceCueIds: derivedSourceIds.filter((id) => validCueIds.has(id) && !excludedSourceCueIds.includes(id)),
		mappedLocalizedSubtitleIds: derivedTargetIds.filter((id) => validSubtitleIds.has(id) && !excludedLocalizedSubtitleIds.includes(id)),
		explicitSourceCueIds,
		explicitLocalizedSubtitleIds,
		excludedSourceCueIds,
		excludedLocalizedSubtitleIds
	};
}

/** Resolve the durable TTS source/target relation without changing edit selection. */
export function resolveTtsSelectionSession(input: {
	anchor: TtsSelectionAnchor;
	selectionItems?: readonly TimelineSelectionItem[];
	cues: readonly VideoLocalizationCue[];
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[];
}): TtsSelectionSession {
	const sameSideItems = (input.selectionItems ?? []).filter((item) =>
		item.kind === 'subtitle' && (
			input.anchor.kind === 'source'
				? item.trackId === 'subtitles'
				: item.trackId === 'localizedSubtitles'
		)
	);

	if (input.anchor.kind === 'target') {
		return materializeSession({
			anchor: input.anchor,
			explicitSourceCueIds: [],
			explicitLocalizedSubtitleIds: unique([
			...sameSideItems.map((item) => item.itemId),
			input.anchor.itemId
			]),
			cues: input.cues,
			localizedSubtitles: input.localizedSubtitles
		});
	}

	return materializeSession({
		anchor: input.anchor,
		explicitSourceCueIds: unique([...sameSideItems.map((item) => item.itemId), input.anchor.itemId]),
		explicitLocalizedSubtitleIds: [],
		cues: input.cues,
		localizedSubtitles: input.localizedSubtitles
	});
}

/**
 * Keep a direct localized-subtitle selection authoritative for TTS.
 *
 * Timeline clicks normally update the edit selection and the TTS relation in
 * one gesture. Inspector/list clicks only provide the subtitle id, so this
 * guard replaces any stale relation from an older timeline position instead
 * of letting the next generation reuse it.
 */
export function ensureDirectTtsTargetSelection(input: {
	current: TtsSelectionSession;
	subtitleId: string;
	selectionItems?: readonly TimelineSelectionItem[];
	cues: readonly VideoLocalizationCue[];
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[];
}): TtsSelectionSession {
	const selectedTargets = (input.selectionItems ?? []).filter((item) =>
		item.kind === 'subtitle'
		&& item.trackId === 'localizedSubtitles'
	);
	if (selectedTargets.some((item) => item.itemId === input.subtitleId)) {
		return resolveTtsSelectionSession({
			anchor: { kind: 'target', itemId: input.subtitleId },
			selectionItems: selectedTargets,
			cues: input.cues,
			localizedSubtitles: input.localizedSubtitles
		});
	}
	if (
		input.current.anchor?.kind === 'target'
		&& input.current.localizedSubtitleIds.includes(input.subtitleId)
	) return input.current;
	return resolveTtsSelectionSession({
		anchor: { kind: 'target', itemId: input.subtitleId },
		selectionItems: [{
			kind: 'subtitle',
			trackId: 'localizedSubtitles',
			itemId: input.subtitleId
		}],
		cues: input.cues,
		localizedSubtitles: input.localizedSubtitles
	});
}

export function updateTtsSelectionSession(input: {
	current: TtsSelectionSession;
	clicked: TtsSelectionAnchor;
	selectionItems: readonly TimelineSelectionItem[];
	additive: boolean;
	cues: readonly VideoLocalizationCue[];
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[];
}): TtsSelectionSession {
	if (!input.additive || !input.current.anchor) {
		return resolveTtsSelectionSession({
			anchor: input.clicked,
			selectionItems: input.selectionItems,
			cues: input.cues,
			localizedSubtitles: input.localizedSubtitles
		});
	}

	if (input.clicked.kind !== input.current.anchor.kind) {
		const sourceSide = input.clicked.kind === 'source';
		const visible = sourceSide
			? unique([...input.current.sourceCueIds, ...input.current.mappedSourceCueIds])
			: unique([...input.current.localizedSubtitleIds, ...input.current.mappedLocalizedSubtitleIds]);
		const excluded = sourceSide ? input.current.excludedSourceCueIds : input.current.excludedLocalizedSubtitleIds;
		const explicit = sourceSide ? input.current.explicitSourceCueIds : input.current.explicitLocalizedSubtitleIds;
		let nextExplicit = explicit;
		let nextExcluded = excluded;
		if (excluded.includes(input.clicked.itemId)) nextExcluded = excluded.filter((id) => id !== input.clicked.itemId);
		else if (explicit.includes(input.clicked.itemId)) nextExplicit = explicit.filter((id) => id !== input.clicked.itemId);
		else if (visible.includes(input.clicked.itemId)) nextExcluded = unique([...excluded, input.clicked.itemId]);
		else nextExplicit = unique([...explicit, input.clicked.itemId]);
		return materializeSession({
			anchor: input.current.anchor,
			explicitSourceCueIds: sourceSide ? nextExplicit : input.current.explicitSourceCueIds,
			explicitLocalizedSubtitleIds: sourceSide ? input.current.explicitLocalizedSubtitleIds : nextExplicit,
			excludedSourceCueIds: sourceSide ? nextExcluded : input.current.excludedSourceCueIds,
			excludedLocalizedSubtitleIds: sourceSide ? input.current.excludedLocalizedSubtitleIds : nextExcluded,
			cues: input.cues,
			localizedSubtitles: input.localizedSubtitles
		});
	}

	const currentExplicit = input.clicked.kind === 'source'
		? input.current.explicitSourceCueIds
		: input.current.explicitLocalizedSubtitleIds;
	const nextExplicit = currentExplicit.includes(input.clicked.itemId)
		? currentExplicit.filter((id) => id !== input.clicked.itemId)
		: unique([...currentExplicit, input.clicked.itemId]);
	const anchorStillSelected = nextExplicit.includes(input.current.anchor.itemId);
	const nextAnchorId = anchorStillSelected ? input.current.anchor.itemId : nextExplicit.at(-1);
	if (!nextAnchorId) return { ...EMPTY_TTS_SELECTION_SESSION };
	return materializeSession({
		anchor: nextAnchorId ? { kind: input.clicked.kind, itemId: nextAnchorId } : null,
		explicitSourceCueIds: input.clicked.kind === 'source' ? nextExplicit : input.current.explicitSourceCueIds,
		explicitLocalizedSubtitleIds: input.clicked.kind === 'target' ? nextExplicit : input.current.explicitLocalizedSubtitleIds,
		excludedSourceCueIds: input.current.excludedSourceCueIds,
		excludedLocalizedSubtitleIds: input.current.excludedLocalizedSubtitleIds,
		cues: input.cues,
		localizedSubtitles: input.localizedSubtitles
	});
}

export function ttsSelectionAnchorIsPassive(
	current: TtsSelectionSession,
	clicked: TtsSelectionAnchor
) {
	if (!current.anchor || current.anchor.kind === clicked.kind) return false;
	const knownIds = clicked.kind === 'source'
		? [
				...current.sourceCueIds,
				...current.mappedSourceCueIds,
				...current.excludedSourceCueIds
			]
		: [
				...current.localizedSubtitleIds,
				...current.mappedLocalizedSubtitleIds,
				...current.excludedLocalizedSubtitleIds
			];
	return knownIds.includes(clicked.itemId);
}

/** Remove deleted IDs and rebuild passive highlights from the latest draft. */
export function refreshTtsSelectionSession(
	current: TtsSelectionSession,
	cues: readonly VideoLocalizationCue[],
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[]
): TtsSelectionSession {
	return materializeSession({
		anchor: current.anchor,
		explicitSourceCueIds: current.explicitSourceCueIds,
		explicitLocalizedSubtitleIds: current.explicitLocalizedSubtitleIds,
		excludedSourceCueIds: current.excludedSourceCueIds,
		excludedLocalizedSubtitleIds: current.excludedLocalizedSubtitleIds,
		cues,
		localizedSubtitles
	});
}

export function ttsSelectionIsContiguous(
	session: TtsSelectionSession,
	localizedSubtitles: readonly VideoLocalizationSubtitleCue[]
) {
	if (session.localizedSubtitleIds.length < 2) return false;
	const orderedIds = localizedSubtitles
		.slice()
		.sort((left, right) => left.start_ms - right.start_ms)
		.map((subtitle) => subtitle.subtitle_id);
	const indexes = session.localizedSubtitleIds
		.map((subtitleId) => orderedIds.indexOf(subtitleId))
		.filter((index) => index >= 0)
		.sort((left, right) => left - right);
	return indexes.length === session.localizedSubtitleIds.length
		&& indexes.every((value, index) => index === 0 || value === indexes[index - 1] + 1);
}

export function ttsSourceSelectionAdvisory(
	session: TtsSelectionSession,
	cues: readonly VideoLocalizationCue[]
): string | null {
	if (session.sourceCueIds.length < 2) return null;
	const ordered = cues
		.filter((cue) => cue.start_ms !== null && cue.end_ms !== null)
		.slice()
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0));
	const selected = new Set(session.sourceCueIds);
	const indexes = ordered
		.map((cue, index) => selected.has(cue.cue_id) ? index : -1)
		.filter((index) => index >= 0);
	if (indexes.length !== session.sourceCueIds.length
		|| indexes.some((value, index) => index > 0 && value !== indexes[index - 1] + 1)) {
		return 'ASR 参考范围包含未单独选择的中间片段，可进入语音合成页后重新调整参考选区';
	}
	const speakers = new Set(ordered
		.filter((cue) => selected.has(cue.cue_id))
		.map((cue) => cue.speaker_id)
		.filter(Boolean));
	if (speakers.size > 1) return 'ASR 参考范围包含多个说话人，可进入语音合成页后重新调整参考选区';
	return null;
}

export function ttsSourceSelectionRange(
	session: TtsSelectionSession,
	cues: readonly VideoLocalizationCue[]
): { startMs: number; endMs: number } | null {
	const selectedIds = new Set(session.sourceCueIds);
	const selected = cues.filter((cue) =>
		selectedIds.has(cue.cue_id)
		&& cue.start_ms !== null
		&& cue.end_ms !== null
	);
	if (!selected.length) return null;
	return {
		startMs: Math.min(...selected.map((cue) => cue.start_ms as number)),
		endMs: Math.max(...selected.map((cue) => cue.end_ms as number))
	};
}
