import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import {
	clientVideoLocalizationUiPatch,
	mergeVideoLocalizationUiStateAfterConflict
} from './ui-state-ownership';
import { mergeVideoLocalizationUiState } from './ui-state-merge';

export type TimelineDirtyField = 'lane' | 'timing';
export type TimelineDirtyFieldsByClipId = ReadonlyMap<string, ReadonlySet<TimelineDirtyField>>;
export type DraftConflictMergeBase = Pick<
	VideoLocalizationDraft,
	| 'cues'
	| 'transcription'
	| 'localized_subtitles'
	| 'localization_state'
	| 'glossary'
	| 'scene_context'
	| 'timeline_clips'
>;
export type DraftConflictMergeOptions = {
	baseDraft?: DraftConflictMergeBase;
	deletedTimelineClipIds?: Iterable<string>;
	timelineDirtyFieldsByClipId?: TimelineDirtyFieldsByClipId;
};

export function snapshotDraftForConflictMerge(value: DraftConflictMergeBase): DraftConflictMergeBase {
	return JSON.parse(JSON.stringify({
		cues: value.cues,
		transcription: value.transcription,
		localized_subtitles: value.localized_subtitles,
		localization_state: value.localization_state,
		glossary: value.glossary,
		scene_context: value.scene_context,
		timeline_clips: value.timeline_clips
	})) as DraftConflictMergeBase;
}

/**
 * A UI-only save is not a content refresh. Keep the content snapshot and its
 * conflict token exactly as they were while adopting the server-owned UI
 * state. Otherwise a stale tab can acquire a fresh `updated_at` token from a
 * UI patch and later overwrite newer timeline content with a full Draft save.
 */
export function preserveClientContentAfterUiStateSave(
	saved: VideoLocalizationDraft,
	current: VideoLocalizationDraft | null,
	submitted: VideoLocalizationDraft
): VideoLocalizationDraft {
	if (!current) return saved;
	const submittedClientState = clientVideoLocalizationUiPatch(submitted.ui_state ?? {});
	const currentClientState = clientVideoLocalizationUiPatch(current.ui_state ?? {});
	const newerClientPatch = Object.fromEntries(
		Object.entries(currentClientState).filter(
			([key, value]) => !valuesEqual(value, submittedClientState[key])
		)
	);
	return {
		...current,
		ui_state: mergeVideoLocalizationUiState(saved.ui_state ?? {}, newerClientPatch)
	};
}

/**
 * The controller packet is the frozen deletion intent for the transaction
 * being saved. Page-owned deletions are retained only for commands that did
 * not originate in that controller.
 */
export function timelineDeletionIntentForSave(
	allPendingDeletedClipIds: Iterable<string>,
	controllerPendingDeletedClipIds: Iterable<string>,
	packetDeletedClipIds: Iterable<string>
) {
	const result = new Set([...allPendingDeletedClipIds].filter(Boolean));
	for (const clipId of controllerPendingDeletedClipIds) result.delete(clipId);
	for (const clipId of packetDeletedClipIds) {
		if (clipId) result.add(clipId);
	}
	return result;
}

const TIMELINE_TIMING_FIELDS = [
	'start_ms',
	'end_ms',
	'source_start_ms',
	'source_end_ms',
	'media_source_clip_id'
] as const;

export function draftForPersistence(
	value: VideoLocalizationDraft,
	options: {
		timelineDirtyFieldsByClipId?: TimelineDirtyFieldsByClipId;
		addedTimelineClipIds?: Iterable<string>;
		deletedTimelineClipIdentities?: ReadonlyMap<string, string>;
	} = {}
): VideoLocalizationDraft {
	const timelineClips = value.timeline_clips.filter((clip) => !clip.optimistic_history_result_id && !clip.optimistic_tts_workflow_id);
	const persistedTimelineClipIds = new Set(timelineClips.map((clip) => clip.clip_id));
	const dubLaneClipIds = [...(options.timelineDirtyFieldsByClipId ?? new Map())]
		.filter(([clipId, fields]) => Boolean(clipId) && fields.has('lane'))
		.map(([clipId]) => clipId);
	const addedTimelineClipIds = [...new Set(options.addedTimelineClipIds ?? [])]
		.filter((clipId) => Boolean(clipId) && persistedTimelineClipIds.has(clipId));
	const deletedTimelineClips = [...(options.deletedTimelineClipIdentities ?? new Map())]
		.filter(([clipId, expectedGenerationIdentity]) => Boolean(clipId && expectedGenerationIdentity))
		.map(([clipId, expectedGenerationIdentity]) => ({
			clip_id: clipId,
			expected_generation_identity: expectedGenerationIdentity
		}));
	const hasExistingEditIntent = Object.prototype.hasOwnProperty.call(value.ui_state ?? {}, 'client_timeline_edit_intent');
	if (
		timelineClips.length === value.timeline_clips.length
		&& !dubLaneClipIds.length
		&& !addedTimelineClipIds.length
		&& !deletedTimelineClips.length
		&& !hasExistingEditIntent
	) return value;
	const { client_timeline_edit_intent: _ignoredEditIntent, ...uiStateWithoutEditIntent } = value.ui_state ?? {};
	return {
		...value,
		timeline_clips: timelineClips,
		ui_state: {
			...uiStateWithoutEditIntent,
			...(dubLaneClipIds.length || addedTimelineClipIds.length || deletedTimelineClips.length
				? {
						client_timeline_edit_intent: {
							...(dubLaneClipIds.length ? { dub_lane_clip_ids: dubLaneClipIds } : {}),
							...(addedTimelineClipIds.length
								? { added_timeline_clip_ids: addedTimelineClipIds }
								: {}),
							...(deletedTimelineClips.length
								? { deleted_timeline_clips: deletedTimelineClips }
								: {})
						}
					}
				: {})
		}
	};
}

export function mergeDraftAfterConflict(
	latest: VideoLocalizationDraft,
	local: VideoLocalizationDraft,
	options: DraftConflictMergeOptions = {}
): VideoLocalizationDraft {
	const base = options.baseDraft;
	const latestLocalizationRevision = String(latest.localization_state?.created_at ?? '');
	const localLocalizationRevision = String(local.localization_state?.created_at ?? '');
	const preserveLatestLocalization = Boolean(
		(latestLocalizationRevision || localLocalizationRevision)
		&& latestLocalizationRevision !== localLocalizationRevision
	);
	const latestTranscriptionRevision = String(latest.transcription?.revision_id ?? '');
	const localTranscriptionRevision = String(local.transcription?.revision_id ?? '');
	const preserveLatestTranscription = Boolean(
		latestTranscriptionRevision && latestTranscriptionRevision !== localTranscriptionRevision
	);
	const latestCues = new Map(latest.cues.map((cue) => [cue.cue_id, cue]));
	const mergedCues = preserveLatestTranscription
		? latest.cues
		: (base
			? mergeEntityCollectionAfterConflict(
					latest.cues,
					local.cues,
					base.cues,
					(cue) => cue.cue_id
				)
			: local.cues.map((cue) => {
				const serverCue = latestCues.get(cue.cue_id);
				if (!serverCue) return cue;
				return {
					...serverCue,
					...cue,
					tts_result_id: serverCue.tts_result_id ?? cue.tts_result_id,
					tts_audio_path: serverCue.tts_audio_path ?? cue.tts_audio_path,
					tts_batch_task_id: serverCue.tts_batch_task_id ?? cue.tts_batch_task_id,
					tts_batch_status: serverCue.tts_batch_status ?? cue.tts_batch_status,
					tts_batch_error: serverCue.tts_batch_error ?? cue.tts_batch_error,
					tts_attempted_at: serverCue.tts_attempted_at ?? cue.tts_attempted_at,
					generated_duration_ms: serverCue.generated_duration_ms ?? cue.generated_duration_ms,
					quality_flags: [...new Set([...(cue.quality_flags ?? []), ...(serverCue.quality_flags ?? [])])]
				};
			}))
			.map((cue) => {
				const serverCue = latestCues.get(cue.cue_id);
				if (!serverCue) return cue;
				const merged = {
					...cue,
					quality_flags: [...new Set([...(cue.quality_flags ?? []), ...(serverCue.quality_flags ?? [])])]
				};
				if (!preserveLatestLocalization) return merged;
				return {
					...merged,
					zh_localized_subtitle_text: serverCue.zh_localized_subtitle_text,
					tts_recommended_text: serverCue.tts_recommended_text
				};
			});
	const mergedLocalizedSubtitles = preserveLatestLocalization
		? latest.localized_subtitles
		: base
			? mergeEntityCollectionAfterConflict(
					latest.localized_subtitles,
					local.localized_subtitles,
					base.localized_subtitles,
					(subtitle) => subtitle.subtitle_id
				).map((subtitle) => {
					const serverSubtitle = latest.localized_subtitles.find(
						(item) => item.subtitle_id === subtitle.subtitle_id
					);
					return serverSubtitle
						? {
								...subtitle,
								quality_flags: [
									...new Set([
										...(subtitle.quality_flags ?? []),
										...(serverSubtitle.quality_flags ?? [])
									])
								]
							}
						: subtitle;
				})
			: local.localized_subtitles;
	const deletedTimelineClipIds = new Set(options.deletedTimelineClipIds ?? []);
	const mergedClips = mergeTimelineClipsAfterConflict(
		latest,
		local,
		base,
		deletedTimelineClipIds,
		options.timelineDirtyFieldsByClipId
	);
	return {
		...latest,
		ui_state: mergeVideoLocalizationUiStateAfterConflict(latest.ui_state ?? {}, local.ui_state ?? {}),
		cues: mergedCues,
		localized_subtitles: mergedLocalizedSubtitles,
		localization_state: preserveLatestLocalization || base ? latest.localization_state : local.localization_state,
		glossary: base
			? mergeEntityCollectionAfterConflict(
					latest.glossary,
					local.glossary,
					base.glossary,
					(entry) => entry.glossary_id
				)
			: local.glossary,
		scene_context: base && valuesEqual(local.scene_context, base.scene_context)
			? latest.scene_context
			: local.scene_context,
		timeline_clips: mergedClips
	};
}

export function mergeTimelineClipAfterConflict(
	serverClip: VideoLocalizationTimelineClip,
	localClip: VideoLocalizationTimelineClip,
	dirtyFields: Iterable<TimelineDirtyField> = []
): VideoLocalizationTimelineClip {
	const dirty = new Set(dirtyFields);
	const merged = { ...serverClip } as VideoLocalizationTimelineClip;
	const mergedRecord = merged as Record<string, unknown>;
	const localRecord = localClip as Record<string, unknown>;
	if (dirty.has('timing')) {
		for (const field of TIMELINE_TIMING_FIELDS) overlayTimelineClipField(mergedRecord, localRecord, field);
	}
	if (dirty.has('lane')) overlayTimelineClipField(mergedRecord, localRecord, 'dub_lane');
	return merged;
}

export function shouldKeepServerResetDraft(latest: VideoLocalizationDraft, stale: VideoLocalizationDraft) {
	return Boolean(
		latest.updated_at
		&& stale.updated_at
		&& latest.updated_at > stale.updated_at
		&& !draftHasResettableContent(latest)
		&& draftHasResettableContent(stale)
	);
}

function overlayTimelineClipField(
	target: Record<string, unknown>,
	source: Record<string, unknown>,
	field: string
) {
	if (Object.prototype.hasOwnProperty.call(source, field)) target[field] = source[field];
	else delete target[field];
}

function mergeEntityCollectionAfterConflict<T extends object>(
	latest: T[],
	local: T[],
	base: T[],
	identity: (value: T) => string
) {
	const latestById = new Map(latest.map((value) => [identity(value), value]));
	const localById = new Map(local.map((value) => [identity(value), value]));
	const baseById = new Map(base.map((value) => [identity(value), value]));
	const mergedById = new Map<string, T>();

	for (const value of latest) mergedById.set(identity(value), value);
	for (const [id, baseValue] of baseById) {
		const localValue = localById.get(id);
		const latestValue = latestById.get(id);
		if (!localValue) {
			mergedById.delete(id);
			continue;
		}
		if (!latestValue) {
			if (!valuesEqual(localValue, baseValue)) mergedById.set(id, localValue);
			continue;
		}
		mergedById.set(id, mergeEntityFieldsAfterConflict(latestValue, localValue, baseValue));
	}
	for (const [id, localValue] of localById) {
		if (baseById.has(id)) continue;
		const latestValue = latestById.get(id);
		mergedById.set(
			id,
			latestValue
				? mergeEntityFieldsAfterConflict(latestValue, localValue, {} as T)
				: localValue
		);
	}

	const baseOrder = base.map(identity);
	const localOrder = local.map(identity);
	const localChangedStructure = !valuesEqual(baseOrder, localOrder);
	const preferredOrder = localChangedStructure ? localOrder : latest.map(identity);
	const result: T[] = [];
	for (const id of preferredOrder) {
		const value = mergedById.get(id);
		if (!value) continue;
		result.push(value);
		mergedById.delete(id);
	}
	for (const value of latest) {
		const id = identity(value);
		const merged = mergedById.get(id);
		if (!merged) continue;
		result.push(merged);
		mergedById.delete(id);
	}
	result.push(...mergedById.values());
	return result;
}

function mergeTimelineClipsAfterConflict(
	latest: VideoLocalizationDraft,
	local: VideoLocalizationDraft,
	base: DraftConflictMergeBase | undefined,
	deletedClipIds: ReadonlySet<string>,
	dirtyFieldsByClipId: TimelineDirtyFieldsByClipId | undefined
) {
	const localById = new Map(local.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const latestById = new Map(latest.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const baseIds = new Set(base?.timeline_clips.map((clip) => clip.clip_id) ?? []);
	const result: VideoLocalizationTimelineClip[] = [];
	for (const serverClip of latest.timeline_clips) {
		if (deletedClipIds.has(serverClip.clip_id)) continue;
		const localClip = localById.get(serverClip.clip_id);
		result.push(
			localClip
				? mergeTimelineClipAfterConflict(
						serverClip,
						localClip,
						dirtyFieldsByClipId?.get(serverClip.clip_id)
					)
				: serverClip
		);
	}
	for (const localClip of local.timeline_clips) {
		if (latestById.has(localClip.clip_id) || deletedClipIds.has(localClip.clip_id)) continue;
		if (base && baseIds.has(localClip.clip_id)) continue;
		result.push(localClip);
	}
	return result;
}

function mergeEntityFieldsAfterConflict<T extends object>(
	latest: T,
	local: T,
	base: T
) {
	const result = { ...latest } as Record<string, unknown>;
	const localRecord = local as Record<string, unknown>;
	const baseRecord = base as Record<string, unknown>;
	for (const field of new Set([...Object.keys(baseRecord), ...Object.keys(localRecord)])) {
		const localHasField = Object.prototype.hasOwnProperty.call(localRecord, field);
		const baseHasField = Object.prototype.hasOwnProperty.call(baseRecord, field);
		const localValue = localRecord[field];
		const baseValue = baseRecord[field];
		if (localHasField === baseHasField && valuesEqual(localValue, baseValue)) continue;
		if (localHasField) result[field] = localValue;
		else delete result[field];
	}
	return result as T;
}

function valuesEqual(left: unknown, right: unknown) {
	if (Object.is(left, right)) return true;
	if (left === undefined || right === undefined) return false;
	return JSON.stringify(left) === JSON.stringify(right);
}

function draftHasResettableContent(currentDraft: VideoLocalizationDraft | null) {
	if (!currentDraft) return false;
	return Boolean(
		currentDraft.source_media.filename
		|| currentDraft.source_media.video_path
		|| currentDraft.source_media.audio_path
		|| currentDraft.stems.original_audio_path
		|| currentDraft.stems.vocals_clean_path
		|| currentDraft.stems.background_path
		|| currentDraft.cues.length
		|| currentDraft.localized_subtitles.length
		|| currentDraft.speakers.length
		|| currentDraft.reference_clips.length
		|| currentDraft.operations.length
	);
}
