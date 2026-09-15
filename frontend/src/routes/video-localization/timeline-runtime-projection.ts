import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import { withTtsWorkflowPlaceholders } from './tts-workflow-placeholders';

/**
 * Runtime clips are view projections. They never belong to the persisted Draft,
 * timeline edit history, lane compaction, or save/delete intent.
 */
export function isTimelineRuntimeClip(clip: VideoLocalizationTimelineClip) {
	return Boolean(clip.optimistic_tts_workflow_id || clip.optimistic_history_result_id);
}

export function durableTimelineClips(clips: readonly VideoLocalizationTimelineClip[]) {
	return clips.filter((clip) => !isTimelineRuntimeClip(clip));
}

export function durableTimelineDraft(value: VideoLocalizationDraft): VideoLocalizationDraft {
	const timelineClips = durableTimelineClips(value.timeline_clips);
	return timelineClips.length === value.timeline_clips.length
		? value
		: { ...value, timeline_clips: timelineClips };
}

/**
 * The only boundary that combines durable timeline state with client runtime
 * overlays for rendering. TTS tasks remain authoritative for workflow status.
 */
export function composeTimelineRuntimeDraft(
	durableDraft: VideoLocalizationDraft,
	runtimeClips: readonly VideoLocalizationTimelineClip[]
): VideoLocalizationDraft {
	const durableClips = durableTimelineClips(durableDraft.timeline_clips);
	const seenClipIds = new Set(durableClips.map((clip) => clip.clip_id));
	// A placement receipt or poll can arrive before its command settles. The
	// saved clip owns that identity immediately; rendering both versions breaks
	// keyed timeline rows. Distinct IDs remain independent editable takes.
	const overlays = runtimeClips.filter((clip) => {
		if (!isTimelineRuntimeClip(clip) || seenClipIds.has(clip.clip_id)) return false;
		seenClipIds.add(clip.clip_id);
		return true;
	});
	return withTtsWorkflowPlaceholders({
		...durableTimelineDraft(durableDraft),
		timeline_clips: [...durableClips, ...overlays]
	});
}

/**
 * Drop terminal/superseded TTS overlays after the authoritative task and
 * timeline projections have converged. History overlays retire as soon as
 * their saved identity is visible, or when their owning command settles.
 */
export function reconcileTimelineRuntimeClips(
	durableDraft: VideoLocalizationDraft,
	runtimeClips: readonly VideoLocalizationTimelineClip[]
) {
	const visibleRuntimeIds = new Set(
		composeTimelineRuntimeDraft(durableDraft, runtimeClips).timeline_clips
			.filter(isTimelineRuntimeClip)
			.map((clip) => clip.clip_id)
	);
	return runtimeClips.filter((clip) => {
		if (!visibleRuntimeIds.delete(clip.clip_id)) return false;
		return true;
	});
}
