import type {
	VideoLocalizationDraft,
	VideoLocalizationTimelineClip,
	VideoLocalizationTimelineProjection
} from '$lib/api/types';
import {
	mergeTimelineClipAfterConflict,
	type TimelineDirtyFieldsByClipId
} from './draft-ownership';

type TimelineProjectionMergeOptions = {
	deletedClipIds?: Iterable<string>;
	addedClipIds?: Iterable<string>;
	dirtyFieldsByClipId?: TimelineDirtyFieldsByClipId;
};

/**
 * Apply the bounded server timeline to the current workspace without replacing
 * ASR, subtitles, selection state, or dedicated task projections.
 */
export function draftWithLiveTimelineProjection(
	current: VideoLocalizationDraft,
	projection: VideoLocalizationTimelineProjection,
	options: TimelineProjectionMergeOptions = {}
): VideoLocalizationDraft {
	const projected = {
		...current,
		timeline_clips: mergeTimelineProjectionClips(
			current.timeline_clips,
			projection.timeline_clips,
			options
		)
	};
	return projected;
}

/**
 * A repository projection is a state delta, not a reason to invalidate every
 * rendered clip. Reuse the existing object for equal clip payloads so keyed
 * Svelte components retain their waveform canvas, media state and observers.
 */
export function mergeTimelineProjectionClips(
	current: readonly VideoLocalizationTimelineClip[],
	projected: readonly VideoLocalizationTimelineClip[],
	options: TimelineProjectionMergeOptions = {}
) {
	const stableCurrent = current.filter(
		(clip) => !clip.optimistic_tts_workflow_id && !clip.optimistic_history_result_id
	);
	const currentById = new Map(stableCurrent.map((clip) => [clip.clip_id, clip]));
	const deletedClipIds = new Set(options.deletedClipIds ?? []);
	const addedClipIds = new Set(options.addedClipIds ?? []);
	const dirtyFieldsByClipId = options.dirtyFieldsByClipId ?? new Map();
	const projectedIds = new Set(projected.map((clip) => clip.clip_id));
	const next = projected
		.filter((clip) => !deletedClipIds.has(clip.clip_id))
		.map((clip) => {
			const currentClip = currentById.get(clip.clip_id);
			const dirtyFields = dirtyFieldsByClipId.get(clip.clip_id);
			return currentClip && dirtyFields?.size
				? mergeTimelineClipAfterConflict(clip, currentClip, dirtyFields)
				: clip;
		});
	for (const currentClip of stableCurrent) {
		if (
			!projectedIds.has(currentClip.clip_id)
			&& !deletedClipIds.has(currentClip.clip_id)
			&& addedClipIds.has(currentClip.clip_id)
		) next.push(currentClip);
	}
	return reconcileTimelineClipPayloads(stableCurrent, next);
}

/**
 * Reconcile a timeline collection by stable clip id. State owners may clone
 * their snapshots for isolation; the render boundary restores structural
 * sharing so an edit to one clip cannot invalidate unrelated media resources.
 */
export function reconcileTimelineClipPayloads(
	current: readonly VideoLocalizationTimelineClip[],
	next: readonly VideoLocalizationTimelineClip[]
) {
	const currentById = new Map(current.map((clip) => [clip.clip_id, clip]));
	return next.map((incoming) => {
		const existing = currentById.get(incoming.clip_id);
		if (!existing) return incoming;
		const candidate = incoming.verification_coverage == null && existing.verification_coverage != null
			? { ...incoming, verification_coverage: existing.verification_coverage }
			: incoming;
		return timelineClipPayloadEqual(existing, candidate) ? existing : candidate;
	});
}

function timelineClipPayloadEqual(
	left: VideoLocalizationTimelineClip,
	right: VideoLocalizationTimelineClip
) {
	const leftKeys = Object.keys(left) as Array<keyof VideoLocalizationTimelineClip>;
	const rightKeys = Object.keys(right) as Array<keyof VideoLocalizationTimelineClip>;
	if (leftKeys.length !== rightKeys.length) return false;
	for (const key of leftKeys) {
		if (!Object.prototype.hasOwnProperty.call(right, key)) return false;
		const leftValue = left[key];
		const rightValue = right[key];
		if (leftValue === rightValue) continue;
		if (
			(leftValue === null || typeof leftValue !== 'object')
			|| (rightValue === null || typeof rightValue !== 'object')
			|| JSON.stringify(leftValue) !== JSON.stringify(rightValue)
		) return false;
	}
	return true;
}

export function repositoryRevisionIsOlder(candidate: string, known: string): boolean {
	if (!/^\d+$/.test(candidate) || !/^\d+$/.test(known)) return false;
	return BigInt(candidate) < BigInt(known);
}
