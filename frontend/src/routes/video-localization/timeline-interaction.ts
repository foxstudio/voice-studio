import type { VideoLocalizationCue, VideoLocalizationSubtitleCue } from '$lib/api/types';
import type { TimelineSelectionItem } from './timeline-context-menu';

export type TimelinePointerIntent = 'ignore' | 'pan' | 'seek' | 'marquee-select' | 'range-create';

export type TimelineSubtitleMergeRequest = {
	track: 'asr' | 'localized';
	itemIds: string[];
	startMs: number;
	endMs: number;
	text: string;
};

export type TimelineGroupMoveCommitItem = TimelineSelectionItem & {
	startMs: number;
	endMs: number;
	sourceStartMs?: number;
	sourceEndMs?: number | null;
	dubLane?: number;
};

export type TimelineSubtitleHitRange = {
	itemId: string;
	startMs: number;
	endMs: number;
};

/**
 * Browsers hit-test absolutely positioned subtitle buttons in CSS pixels, while
 * the timeline owns their meaning in milliseconds. On a long, zoomed-out
 * timeline several adjacent cues can occupy the same fractional pixel. Keep the
 * DOM target when its real interval contains the pointer; otherwise recover the
 * semantic cue at that timeline time.
 */
export function resolveTimelineSubtitleHit(
	pointerTimeMs: number,
	domTargetItemId: string,
	ranges: TimelineSubtitleHitRange[]
): string {
	if (!Number.isFinite(pointerTimeMs)) return domTargetItemId;
	const validRanges = ranges.filter((range) => (
		range.itemId
		&& Number.isFinite(range.startMs)
		&& Number.isFinite(range.endMs)
		&& range.endMs > range.startMs
	));
	const contains = (range: TimelineSubtitleHitRange) => (
		pointerTimeMs >= range.startMs && pointerTimeMs < range.endMs
	);
	const domTarget = validRanges.find((range) => range.itemId === domTargetItemId);
	if (domTarget && contains(domTarget)) return domTargetItemId;

	const semanticTargets = validRanges
		.filter(contains)
		.sort((left, right) => {
			const durationDelta = (left.endMs - left.startMs) - (right.endMs - right.startMs);
			if (durationDelta) return durationDelta;
			const leftDistance = Math.abs((left.startMs + left.endMs) / 2 - pointerTimeMs);
			const rightDistance = Math.abs((right.startMs + right.endMs) / 2 - pointerTimeMs);
			return leftDistance - rightDistance
				|| left.startMs - right.startMs
				|| left.itemId.localeCompare(right.itemId);
		});
	return semanticTargets[0]?.itemId ?? domTargetItemId;
}

export function resolveTimelineSubtitleMerge(
	selection: TimelineSelectionItem[],
	asrCues: VideoLocalizationCue[],
	localizedSubtitles: VideoLocalizationSubtitleCue[]
): TimelineSubtitleMergeRequest | null {
	if (selection.length < 2 || selection.some((item) => item.kind !== 'subtitle')) return null;
	const trackId = selection[0]?.trackId;
	if ((trackId !== 'subtitles' && trackId !== 'localizedSubtitles') || selection.some((item) => item.trackId !== trackId)) return null;

	const selectedIds = new Set(selection.map((item) => item.itemId));
	const items = trackId === 'subtitles'
		? asrCues.flatMap((cue) => cue.start_ms === null || cue.end_ms === null || !selectedIds.has(cue.cue_id) ? [] : [{
			id: cue.cue_id,
			startMs: cue.start_ms,
			endMs: cue.end_ms,
			text: cue.en_subtitle_text?.trim() || cue.source_text_raw?.trim() || ''
		}])
		: localizedSubtitles.flatMap((subtitle) => !selectedIds.has(subtitle.subtitle_id) ? [] : [{
			id: subtitle.subtitle_id,
			startMs: subtitle.start_ms,
			endMs: subtitle.end_ms,
			text: subtitle.text.trim()
		}]);
	if (items.length !== selectedIds.size || items.length < 2) return null;

	items.sort((left, right) => left.startMs - right.startMs || left.endMs - right.endMs || left.id.localeCompare(right.id));
	return {
		track: trackId === 'subtitles' ? 'asr' : 'localized',
		itemIds: items.map((item) => item.id),
		startMs: Math.min(...items.map((item) => item.startMs)),
		endMs: Math.max(...items.map((item) => item.endMs)),
		text: items.map((item) => item.text).filter(Boolean).join('\n')
	};
}

export function isRepeatedPrimaryPress({
	detail,
	elapsedMs,
	distancePx,
	maximumDelayMs = 360,
	maximumDistancePx = 7
}: {
	detail: number;
	elapsedMs: number | null;
	distancePx: number | null;
	maximumDelayMs?: number;
	maximumDistancePx?: number;
}) {
	if (detail >= 2) return true;
	return elapsedMs !== null && distancePx !== null
		&& elapsedMs >= 0 && elapsedMs <= maximumDelayMs
		&& distancePx <= maximumDistancePx;
}

export function timelinePointerIntent({
	button,
	detail = 1,
	overTimeline,
	overTrack,
	interactive
}: {
	button: number;
	detail?: number;
	overTimeline: boolean;
	overTrack: boolean;
	interactive: boolean;
}): TimelinePointerIntent {
	if (button === 1) return overTimeline ? 'pan' : 'ignore';
	if (button !== 0 || interactive) return 'ignore';
	if (overTrack) return detail >= 2 ? 'range-create' : 'marquee-select';
	return 'seek';
}
