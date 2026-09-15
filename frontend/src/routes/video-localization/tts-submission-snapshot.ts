import type { VideoLocalizationSubtitleCue, VideoLocalizationTimelineClip } from '$lib/api/types';

export type TtsSubmissionSnapshot = {
	clientId: string;
	segmentId: string;
	subtitles: VideoLocalizationSubtitleCue[];
	targetClip: VideoLocalizationTimelineClip | null;
	sourceCueIds: string[];
	localizedSubtitleIds: string[];
	primaryCueId: string | null;
	startMs: number;
	endMs: number;
	summary: string;
};

type SnapshotInput = {
	clientId: string;
	selectedSubtitle: VideoLocalizationSubtitleCue | null;
	selectedSubtitles: VideoLocalizationSubtitleCue[];
	selectedClip: VideoLocalizationTimelineClip | null;
	groupedClipSegmentId: string;
	sourceCueIds?: string[];
	localizedSubtitleIds?: string[];
};

function copySubtitle(item: VideoLocalizationSubtitleCue): VideoLocalizationSubtitleCue {
	return {
		...item,
		source_cue_ids: [...(item.source_cue_ids ?? [])],
		source_word_ids: [...(item.source_word_ids ?? [])],
		quality_flags: [...(item.quality_flags ?? [])]
	};
}

export function buildTtsSubmissionSnapshot(input: SnapshotInput): TtsSubmissionSnapshot | null {
	const requestedSubtitleIds = new Set(input.localizedSubtitleIds ?? []);
	const sessionSubtitles = requestedSubtitleIds.size
		? input.selectedSubtitles.filter((item) => requestedSubtitleIds.has(item.subtitle_id))
		: input.selectedSubtitles;
	const selectedSubtitles = sessionSubtitles.length
		? sessionSubtitles.map(copySubtitle)
		: input.selectedSubtitle
			? [copySubtitle(input.selectedSubtitle)]
			: [];
	const first = selectedSubtitles[0] ?? null;
	const last = selectedSubtitles.at(-1) ?? null;
	if (!selectedSubtitles.length) return null;
	const segmentId = input.groupedClipSegmentId && input.selectedClip
		? input.groupedClipSegmentId
		: selectedSubtitles.length > 1
			? `group_${first!.subtitle_id}_${last!.subtitle_id}_${selectedSubtitles.length}`
			: first!.subtitle_id;
	if (!segmentId) return null;

	const targetClip = input.selectedClip?.track_id === 'dub' && (
		input.selectedClip.subtitle_id === segmentId ||
		input.groupedClipSegmentId === segmentId ||
		(!input.selectedClip.subtitle_id && input.selectedClip.cue_id === segmentId)
	)
		? { ...input.selectedClip, source_cue_ids: [...(input.selectedClip.source_cue_ids ?? [])] }
		: null;
	const sourceCueIds = Array.from(new Set(
		input.sourceCueIds?.length
			? input.sourceCueIds
			: selectedSubtitles.length
			? selectedSubtitles.flatMap((item) => item.source_cue_ids ?? (item.linked_cue_id ? [item.linked_cue_id] : []))
				: targetClip?.source_cue_ids ?? (targetClip?.cue_id ? [targetClip.cue_id] : [])
	));
	const startMs = Math.max(0, first!.start_ms);
	const endMs = Math.max(
		startMs + 300,
		last!.end_ms
	);
	const summary = selectedSubtitles.map((item) => item.tts_text?.trim() || item.text.trim()).filter(Boolean).join(' ');

	return {
		clientId: input.clientId,
		segmentId,
		subtitles: selectedSubtitles,
		targetClip,
		sourceCueIds,
		localizedSubtitleIds: selectedSubtitles.map((item) => item.subtitle_id),
		primaryCueId: sourceCueIds[0] ?? targetClip?.cue_id ?? null,
		startMs,
		endMs,
		summary
	};
}
