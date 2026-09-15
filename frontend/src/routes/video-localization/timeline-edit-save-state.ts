import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import type {
	TimelineDirtyField,
	TimelineDirtyFieldsByClipId
} from './timeline-edit-controller';

export function snapshotTimelineDirtyFields(source: TimelineDirtyFieldsByClipId) {
	return new Map([...source].map(([clipId, fields]) => [clipId, new Set(fields)]));
}

function clipFieldMatches(
	left: VideoLocalizationTimelineClip | undefined,
	right: VideoLocalizationTimelineClip | undefined,
	field: string
) {
	if (!left || !right) return left === right;
	const leftRecord = left as Record<string, unknown>;
	const rightRecord = right as Record<string, unknown>;
	const leftHasField = Object.prototype.hasOwnProperty.call(leftRecord, field);
	const rightHasField = Object.prototype.hasOwnProperty.call(rightRecord, field);
	return leftHasField === rightHasField && (!leftHasField || Object.is(leftRecord[field], rightRecord[field]));
}

function dirtyFieldMatches(
	field: TimelineDirtyField,
	left: VideoLocalizationTimelineClip | undefined,
	right: VideoLocalizationTimelineClip | undefined
) {
	if (field === 'lane') return clipFieldMatches(left, right, 'dub_lane');
	return ['start_ms', 'end_ms', 'source_start_ms', 'source_end_ms', 'media_source_clip_id']
		.every((key) => clipFieldMatches(left, right, key));
}

export function clearPersistedTimelineDirtyFields(
	target: Map<string, Set<TimelineDirtyField>>,
	persisted: TimelineDirtyFieldsByClipId,
	savedDraft: VideoLocalizationDraft,
	currentDraft: VideoLocalizationDraft | null
) {
	const savedClips = new Map(savedDraft.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const currentClips = new Map((currentDraft?.timeline_clips ?? []).map((clip) => [clip.clip_id, clip]));
	for (const [clipId, fields] of persisted) {
		const currentFields = target.get(clipId);
		if (!currentFields) continue;
		for (const field of fields) {
			if (dirtyFieldMatches(field, savedClips.get(clipId), currentClips.get(clipId))) currentFields.delete(field);
		}
		if (!currentFields.size) target.delete(clipId);
	}
}
