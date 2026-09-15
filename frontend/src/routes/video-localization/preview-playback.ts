import type { VideoLocalizationTimelineClip } from '$lib/api/types';

export const AUDIO_DRIFT_TOLERANCE_SECONDS = 0.06;
export const AUDIO_HARD_SYNC_THRESHOLD_SECONDS = 0.6;
export const AUDIO_MAX_RATE_ADJUSTMENT = 0.05;

export function timelineClipHasAudioSource(clip: VideoLocalizationTimelineClip) {
	return Boolean(clip.has_audio_source || clip.audio_path || clip.media_source_clip_id || clip.clip_id === 'media_original');
}

export function timelineClipKey(clip: VideoLocalizationTimelineClip) {
	return `${clip.clip_id}:${clip.result_id || clip.generation_identity || clip.audio_path || clip.media_source_clip_id || ''}`;
}

export function timelineClipEndMs(clip: VideoLocalizationTimelineClip) {
	const start = Math.max(0, clip.start_ms ?? 0);
	return Math.max(start, clip.end_ms ?? start + 1800);
}

export type TimelineClipPlayableRange = {
	timelineStartMs: number;
	timelineEndMs: number;
	sourceStartMs: number;
	sourceEndMs: number;
};

/**
 * The editor can retain a deliberate timeline placement whose source crop is
 * shorter (or longer). Playback always stays at 1x, so it may only read their
 * shared duration. The remaining timeline tail is silence; this projection
 * never changes the saved clip.
 */
export function timelineClipPlayableRange(
	clip: VideoLocalizationTimelineClip
): TimelineClipPlayableRange {
	const timelineStartMs = Math.max(0, Number(clip.start_ms ?? 0));
	const timelineEndMs = Math.max(timelineStartMs, timelineClipEndMs(clip));
	const sourceStartMs = Math.max(0, Number(clip.source_start_ms ?? 0));
	const sourceEndMs = Math.max(
		sourceStartMs,
		Number(clip.source_end_ms ?? sourceStartMs + timelineEndMs - timelineStartMs)
	);
	const playableDurationMs = Math.max(0, Math.min(
		timelineEndMs - timelineStartMs,
		sourceEndMs - sourceStartMs
	));
	return {
		timelineStartMs,
		timelineEndMs: timelineStartMs + playableDurationMs,
		sourceStartMs,
		sourceEndMs: sourceStartMs + playableDurationMs
	};
}

export function activeTimelineClips(
	clips: VideoLocalizationTimelineClip[],
	trackId: string,
	timeMs: number
) {
	return clips.filter((clip) => {
		if (clip.track_id !== trackId || !timelineClipHasAudioSource(clip)) return false;
		const range = timelineClipPlayableRange(clip);
		return timeMs >= range.timelineStartMs && timeMs < range.timelineEndMs;
	});
}

export function clipSourceTimeSeconds(clip: VideoLocalizationTimelineClip, timelineTimeSeconds: number) {
	const range = timelineClipPlayableRange(clip);
	const sourceTimeMs = range.sourceStartMs + Math.max(
		0,
		timelineTimeSeconds * 1000 - range.timelineStartMs
	);
	return Math.min(range.sourceEndMs, sourceTimeMs) / 1000;
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
		.filter((clip) => (
			clip.track_id === trackId
			&& timelineClipHasAudioSource(clip)
			&& timelineClipPlayableRange(clip).timelineEndMs > timeMs
		))
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0))
		.slice(0, Math.max(0, limit));
}

export function scheduledTimelineClips(
	clips: VideoLocalizationTimelineClip[],
	trackId: string,
	timeMs: number,
	options: {
		futureLimit?: number;
		lookaheadMs?: number;
	} = {}
) {
	const futureLimit = Math.max(0, Math.floor(options.futureLimit ?? 4));
	const lookaheadMs = Math.max(0, Math.round(options.lookaheadMs ?? 10_000));
	const active = activeTimelineClips(clips, trackId, timeMs)
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0));
	const activeKeys = new Set(active.map(timelineClipKey));
	const future = clips
		.filter((clip) => (
			clip.track_id === trackId
			&& timelineClipHasAudioSource(clip)
			&& !activeKeys.has(timelineClipKey(clip))
			&& (clip.start_ms ?? 0) > timeMs
			&& (clip.start_ms ?? 0) - timeMs <= lookaheadMs
		))
		.sort((left, right) => (left.start_ms ?? 0) - (right.start_ms ?? 0))
		.slice(0, futureLimit);
	return [...active, ...future];
}
