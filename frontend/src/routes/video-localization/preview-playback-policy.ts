import type { ProjectMediaHealth } from '$lib/api/types';

export function previewMediaIdentityKey(
	projectId: string,
	mediaHealth: ProjectMediaHealth | null
) {
	return [
		projectId,
		mediaHealth?.source_video.revision ?? mediaHealth?.source_video.status ?? '',
		mediaHealth?.source_audio.revision ?? mediaHealth?.source_audio.status ?? '',
		mediaHealth?.vocals.revision ?? mediaHealth?.vocals.status ?? '',
		mediaHealth?.background.revision ?? mediaHealth?.background.status ?? ''
	].join(':');
}

export function isMediaAbortError(error: unknown) {
	return error instanceof Error && error.name === 'AbortError';
}

export function playbackRequestStillCurrent({
	requestRevision,
	intentRevision,
	pendingRevision,
	playbackWanted
}: {
	requestRevision: number;
	intentRevision: number;
	pendingRevision?: number;
	playbackWanted: boolean;
}) {
	return requestRevision === intentRevision
		&& (pendingRevision === undefined || pendingRevision === requestRevision)
		&& playbackWanted;
}

export function mediaClockAdvanced(
	currentTime: number,
	stalledTime: number,
	minimumAdvanceSeconds = 0.02
) {
	return Number.isFinite(currentTime)
		&& Number.isFinite(stalledTime)
		&& Math.abs(currentTime - stalledTime) >= minimumAdvanceSeconds;
}

export function shouldRestartPendingPlayback(playbackWanted: boolean, videoPaused: boolean) {
	return playbackWanted && videoPaused;
}

export function shouldCommitMediaPause(videoPaused: boolean, videoEnded: boolean) {
	return videoPaused || videoEnded;
}

export function shouldFallbackFromAudioPreview(
	variant: 'source' | 'preview'
) {
	return variant === 'preview';
}

export function previewPlaybackModeLabel({
	activeTrackLabels,
	hasSoloTrack,
	preparing,
	blockers = []
}: {
	activeTrackLabels: string[];
	hasSoloTrack: boolean;
	preparing: boolean;
	blockers?: string[];
}) {
	if (!activeTrackLabels.length) return '静音预览';
	const activeTracks = activeTrackLabels.join(' + ');
	if (!preparing) return hasSoloTrack ? `${activeTracks}（独奏）` : activeTracks;
	return blockers.length
		? `正在缓冲 · ${blockers.join(' + ')}`
		: `正在准备音频 · ${activeTracks}`;
}
