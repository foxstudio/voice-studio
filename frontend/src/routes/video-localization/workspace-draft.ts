import type {
	ProjectMediaHealth,
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip
} from '$lib/api/types';
import { ensureDubTrackLaneMetadata } from './dub-track-lanes';
import { attachAvailableMediaTracks } from './media-track-projection';
import { durableTimelineDraft } from './timeline-runtime-projection';

export function isOrphanedDubPlaceholder(clip: VideoLocalizationTimelineClip) {
	return (
		clip.track_id === 'dub'
		&& ['queued', 'processing', 'applying'].includes(String(clip.status ?? ''))
		&& !clip.audio_path
		&& !clip.task_id
		&& !clip.generation_id
		&& !clip.result_id
		&& !clip.optimistic_tts_workflow_id
	);
}

export function normalizeWorkspaceDraft(
	value: VideoLocalizationDraft,
	health: ProjectMediaHealth | null | undefined
): VideoLocalizationDraft {
	const durableValue = durableTimelineDraft(value);
	const retainedTimelineClips = durableValue.timeline_clips.filter(
		(clip) => !isOrphanedDubPlaceholder(clip)
	);
	const baseValue = retainedTimelineClips.length === durableValue.timeline_clips.length
		? durableValue
		: { ...durableValue, timeline_clips: retainedTimelineClips };
	const nextValue = attachAvailableMediaTracks(baseValue, health);
	const stableTimelineClips = ensureDubTrackLaneMetadata(nextValue.timeline_clips);
	if (stableTimelineClips === nextValue.timeline_clips) return nextValue;
	return {
		...nextValue,
		timeline_clips: stableTimelineClips
	};
}
