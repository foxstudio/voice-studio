import type { VideoLocalizationTimelineClip } from '$lib/api/types';

export const AUDIO_DRIFT_TOLERANCE_SECONDS = 0.06;
export const AUDIO_HARD_SYNC_THRESHOLD_SECONDS = 0.6;
export const AUDIO_MAX_RATE_ADJUSTMENT = 0.05;

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

export function shouldCorrectAudioDrift(
	currentTime: number,
	targetTime: number,
	tolerance = AUDIO_DRIFT_TOLERANCE_SECONDS
) {
	return !Number.isFinite(currentTime) || Math.abs(currentTime - targetTime) > tolerance;
}

export function shouldHardCorrectAudioDrift(currentTime: number, targetTime: number) {
	return shouldCorrectAudioDrift(currentTime, targetTime, AUDIO_HARD_SYNC_THRESHOLD_SECONDS);
}

export function audioPlaybackRateForDrift(currentTime: number, targetTime: number) {
	if (!Number.isFinite(currentTime) || !Number.isFinite(targetTime)) return 1;
	const drift = targetTime - currentTime;
	if (Math.abs(drift) <= AUDIO_DRIFT_TOLERANCE_SECONDS || Math.abs(drift) >= AUDIO_HARD_SYNC_THRESHOLD_SECONDS) return 1;
	const adjustment = Math.max(-AUDIO_MAX_RATE_ADJUSTMENT, Math.min(AUDIO_MAX_RATE_ADJUSTMENT, drift * 0.25));
	return 1 + adjustment;
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
