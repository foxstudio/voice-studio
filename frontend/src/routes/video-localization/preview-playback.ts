import type { VideoLocalizationTimelineClip } from '$lib/api/types';

export const AUDIO_DRIFT_TOLERANCE_SECONDS = 0.12;

export function timelineClipKey(clip: VideoLocalizationTimelineClip) {
	return `${clip.clip_id}:${clip.audio_path ?? ''}`;
}

export function activeTimelineClips(
	clips: VideoLocalizationTimelineClip[],
	trackId: string,
	timeMs: number
) {
	return clips.filter((clip) => {
		if (clip.track_id !== trackId || !clip.audio_path) return false;
		const start = clip.start_ms ?? 0;
		const end = clip.end_ms ?? start + 1800;
		return timeMs >= start && timeMs < end;
	});
}

export function clipSourceTimeSeconds(clip: VideoLocalizationTimelineClip, timelineTimeSeconds: number) {
	const sourceStart = Math.max(0, (clip.source_start_ms ?? 0) / 1000);
	const timelineStart = Math.max(0, (clip.start_ms ?? 0) / 1000);
	return sourceStart + Math.max(0, timelineTimeSeconds - timelineStart);
}

export function shouldCorrectAudioDrift(currentTime: number, targetTime: number) {
	return !Number.isFinite(currentTime) || Math.abs(currentTime - targetTime) > AUDIO_DRIFT_TOLERANCE_SECONDS;
}

export function upcomingTimelineClips(
	clips: VideoLocalizationTimelineClip[],
	trackId: string,
	timeMs: number,
	limit = 4
) {
	return clips
		.filter((clip) => clip.track_id === trackId && Boolean(clip.audio_path) && (clip.end_ms ?? 0) > timeMs)
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0))
		.slice(0, Math.max(0, limit));
}
