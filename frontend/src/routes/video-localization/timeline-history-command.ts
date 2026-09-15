import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip,
	VideoLocalizationTimelineMutationResponse
} from '$lib/api/types';

export function timelineDubSegmentId(clip: VideoLocalizationTimelineClip | null | undefined) {
	if (clip?.track_id !== 'dub') return '';
	return clip.subtitle_id || (clip.clip_id.startsWith('clip_group_')
		? clip.clip_id.slice('clip_'.length) : '') || clip.cue_id || '';
}

export function resolveHistoryTimelineClip(
	clips: VideoLocalizationTimelineClip[],
	selected: VideoLocalizationTimelineClip | null | undefined,
	segmentId: string,
	allowCueFallback: boolean
) {
	if (!segmentId) return null;
	if (selected && timelineDubSegmentId(selected) === segmentId) {
		return clips.find((clip) => clip.clip_id === selected.clip_id && timelineDubSegmentId(clip) === segmentId) ?? null;
	}
	return clips.find((clip) => clip.track_id === 'dub' &&
		(clip.subtitle_id === segmentId || (allowCueFallback && clip.cue_id === segmentId))) ?? null;
}

export function createHistoryTimelineClipId(randomUuid: string) {
	const token = randomUuid.replace(/[^A-Za-z0-9]/g, '');
	if (!token) throw new Error('无法创建时间线片段 ID');
	return `history_clip_${token}`;
}

export function mergeTimelineMutation(
	draft: VideoLocalizationDraft,
	result: VideoLocalizationTimelineMutationResponse
): VideoLocalizationDraft {
	const affectedIds = new Set(result.affected_clip_ids);
	const clipUpdates = new Map(result.timeline_clips.map((clip) => [clip.clip_id, clip]));
	const cueUpdates = new Map(result.cues.map((cue) => [cue.cue_id, cue]));
	const subtitleUpdates = new Map(
		result.localized_subtitles.map((subtitle) => [subtitle.subtitle_id, subtitle])
	);
	const existingClipIds = new Set<string>();
	const nextClips = draft.timeline_clips
		.filter((clip) => !affectedIds.has(clip.clip_id) || clipUpdates.has(clip.clip_id))
		.map((clip) => {
			existingClipIds.add(clip.clip_id);
			return clipUpdates.get(clip.clip_id) ?? clip;
		});
	for (const clip of result.timeline_clips) {
		if (!existingClipIds.has(clip.clip_id)) nextClips.push(clip);
	}
	return {
		...draft,
		updated_at: result.updated_at,
		cues: draft.cues.map((cue) => cueUpdates.get(cue.cue_id) ?? cue),
		localized_subtitles: draft.localized_subtitles.map(
			(subtitle) => subtitleUpdates.get(subtitle.subtitle_id) ?? subtitle
		),
		timeline_clips: nextClips,
		ui_state: {
			...draft.ui_state,
			dub_lane_states: result.dub_lane_states,
			discarded_tts_task_ids: result.discarded_tts_task_ids
		}
	};
}
