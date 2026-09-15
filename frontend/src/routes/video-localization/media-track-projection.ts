import type { ProjectMediaHealth, VideoLocalizationDraft, VideoLocalizationTimelineClip } from '$lib/api/types';
import { mediaTrackResourceId } from './media-health';

type ProjectMediaTrack = {
	trackId: 'original' | 'vocals' | 'background';
	resourceId: string | null | undefined;
};

/**
 * Media health owns server resource identity; timeline clips own arrangement.
 * This projector may attach an available media asset to its default track, but
 * it must never change mute, solo, gain, or any other user-owned mix state.
 */
export function attachAvailableMediaTracks(
	value: VideoLocalizationDraft,
	health: ProjectMediaHealth | null | undefined
): VideoLocalizationDraft {
	const durationMs = Math.max(300, Math.round(value.source_media.duration_ms ?? 0));
	const disabledTracks = new Set(
		Array.isArray(value.ui_state?.disabled_media_tracks)
			? value.ui_state.disabled_media_tracks.map(String)
			: []
	);
	const mediaTracks: ProjectMediaTrack[] = [
		{ trackId: 'original', resourceId: mediaTrackResourceId(health, 'original') },
		{ trackId: 'vocals', resourceId: mediaTrackResourceId(health, 'vocals') },
		{ trackId: 'background', resourceId: mediaTrackResourceId(health, 'background') }
	];
	const additions: VideoLocalizationTimelineClip[] = [];
	for (const track of mediaTracks) {
		if (
			!track.resourceId
			|| disabledTracks.has(track.trackId)
			|| value.timeline_clips.some((clip) => clip.track_id === track.trackId)
		) continue;
		const mediaClipId = `media_${track.trackId}`;
		additions.push({
			clip_id: mediaClipId,
			media_source_clip_id: mediaClipId,
			track_id: track.trackId,
			start_ms: 0,
			end_ms: durationMs,
			source_start_ms: 0,
			source_end_ms: durationMs,
			status: 'ready',
			media_clip: true
		});
	}
	if (!additions.length) return value;
	return { ...value, timeline_clips: [...value.timeline_clips, ...additions] };
}
