export type SelectionPlaybackDecision = {
	/** Where the UI playhead should be shown after applying the playback rule. */
	nextPosition: number;
	/** Whether normal IN/OUT boundary handling should run on this tick. */
	enforceSelection: boolean;
	/** True only when a settled selection puts the current playhead outside it. */
	shouldRestartAtStart: boolean;
	/** The settled playhead is exactly at (or has reached) OUT. */
	atSelectionEnd: boolean;
};

/**
 * Decide where a preview resumes when a user releases an IN/OUT control.
 *
 * A normal release only restarts when the playhead falls outside the new
 * range.  The one deliberate exception is an enabled loop released exactly
 * at the source/OUT end: that is the same finished selection boundary that
 * normally loops back to IN, so it must restart rather than remain ended.
 */
export function resolveSelectionReleasePlayback(
	currentTime: number,
	start: number,
	end: number,
	sourceEnded: boolean,
	loopEnabled: boolean
): SelectionPlaybackDecision {
	const selection = resolveSelectionPlayback(currentTime, start, end, false);
	const shouldRestartAtStart = selection.shouldRestartAtStart || (sourceEnded && loopEnabled && selection.atSelectionEnd);
	return {
		...selection,
		nextPosition: shouldRestartAtStart ? Math.min(start, end) : selection.nextPosition,
		shouldRestartAtStart
	};
}

/**
 * Give every selection-preview timeline the same playback rule.
 *
 * - While an IN/OUT adjustment is held, `enforceSelection` is false: the
 *   source audio keeps playing naturally, even outside the temporary range.
 * - When the adjustment settles, a playhead inside the new inclusive range
 *   continues from its current position; a playhead outside restarts at IN.
 */
export function resolveSelectionPlayback(currentTime: number, start: number, end: number, isEditing: boolean): SelectionPlaybackDecision {
	const selectionStart = Math.min(start, end);
	const selectionEnd = Math.max(start, end);
	const fallback = Number.isFinite(selectionStart) ? selectionStart : 0;
	const sourcePosition = Number.isFinite(currentTime) ? currentTime : fallback;
	if (isEditing) return { nextPosition: sourcePosition, enforceSelection: false, shouldRestartAtStart: false, atSelectionEnd: false };
	const remainsInSelection = sourcePosition >= selectionStart && sourcePosition <= selectionEnd;
	return {
		nextPosition: remainsInSelection ? sourcePosition : fallback,
		enforceSelection: true,
		shouldRestartAtStart: !remainsInSelection,
		atSelectionEnd: remainsInSelection && sourcePosition >= selectionEnd
	};
}
