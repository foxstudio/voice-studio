import type { VideoLocalizationTrackId } from './studio-state';

export type TimelineDeletionState = {
	kind: 'subtitle' | 'audio';
	trackId: VideoLocalizationTrackId;
	trackLocked: boolean;
	laneLocked: boolean;
	runtimeBusy: boolean;
	ttsWorkflowMarker: string | null | undefined;
};

/**
 * Runtime work normally locks a timeline item. Active TTS placeholders are the
 * exception: deleting one is the user's explicit request to stop its workflow
 * and remove the fragment. Explicit track/lane locks still win.
 */
export function timelineItemDeletionBlocked(state: TimelineDeletionState) {
	if (state.trackLocked || state.laneLocked) return true;
	if (!state.runtimeBusy) return false;
	return !(
		state.kind === 'audio'
		&& state.trackId === 'dub'
		&& Boolean(state.ttsWorkflowMarker)
	);
}
