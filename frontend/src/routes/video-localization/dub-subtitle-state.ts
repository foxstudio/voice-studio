import type { VideoLocalizationDubSubtitleCue } from '$lib/api/types';
import { subtitleCueDragBounds } from './studio-state';

export const DUB_SUBTITLE_SOURCE_CHANGED_FLAG = 'stale:source-changed';

export function dubSubtitleTimingBounds(
	subtitles: VideoLocalizationDubSubtitleCue[],
	subtitleId: string,
	timelineDurationMs: number
) {
	const selected = subtitles.find((item) => item.subtitle_id === subtitleId);
	if (!selected) return { minStartMs: 0, maxEndMs: Math.max(0, timelineDurationMs) };
	const lanes = new Set(selected.dub_lanes);
	const sameLaneCues = subtitles
		.filter((item) => item.dub_lanes.some((lane) => lanes.has(lane)))
		.map((item) => ({
			cue_id: item.subtitle_id,
			start_ms: item.start_ms,
			end_ms: item.end_ms
		}));
	return subtitleCueDragBounds(
		sameLaneCues,
		subtitleId,
		Math.max(timelineDurationMs, selected.end_ms)
	);
}
