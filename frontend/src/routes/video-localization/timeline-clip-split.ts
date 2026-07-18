import type { VideoLocalizationTimelineClip } from '$lib/api/types';

export const MIN_AUDIO_CLIP_DURATION_MS = 300;

export function nextSplitClipId(clips: VideoLocalizationTimelineClip[], baseId: string) {
	const used = new Set(clips.map((clip) => clip.clip_id));
	let index = 2;
	let candidate = `${baseId}_part_${index}`;
	while (used.has(candidate)) {
		index += 1;
		candidate = `${baseId}_part_${index}`;
	}
	return candidate;
}

export function splitTimelineAudioClip(
	clips: VideoLocalizationTimelineClip[],
	clipId: string,
	splitMs: number,
	options: { nextClipId?: string; nextSubtitleId?: string } = {}
) {
	const clip = clips.find((item) => item.clip_id === clipId);
	if (!clip) return null;
	const startMs = Math.max(0, Math.round(clip.start_ms ?? 0));
	const endMs = Math.max(startMs + MIN_AUDIO_CLIP_DURATION_MS, Math.round(clip.end_ms ?? startMs + 1800));
	const cutMs = Math.round(splitMs);
	if (cutMs < startMs + MIN_AUDIO_CLIP_DURATION_MS || cutMs > endMs - MIN_AUDIO_CLIP_DURATION_MS) return null;

	const sourceStartMs = Math.max(0, Math.round(clip.source_start_ms ?? 0));
	const sourceEndMs = Math.max(
		sourceStartMs + MIN_AUDIO_CLIP_DURATION_MS,
		Math.round(clip.source_end_ms ?? sourceStartMs + endMs - startMs)
	);
	const sourceSplitMs = Math.min(sourceEndMs, sourceStartMs + (cutMs - startMs));
	const mediaSourceClipId = String(clip.media_source_clip_id || clip.clip_id);
	const first: VideoLocalizationTimelineClip = {
		...clip,
		media_source_clip_id: mediaSourceClipId,
		end_ms: cutMs,
		source_end_ms: sourceSplitMs
	};
	const second: VideoLocalizationTimelineClip = {
		...clip,
		clip_id: options.nextClipId ?? nextSplitClipId(clips, clip.clip_id),
		media_source_clip_id: mediaSourceClipId,
		...(options.nextSubtitleId ? { subtitle_id: options.nextSubtitleId } : {}),
		start_ms: cutMs,
		end_ms: endMs,
		source_start_ms: sourceSplitMs,
		source_end_ms: sourceEndMs
	};

	return {
		first,
		second,
		clips: clips.flatMap((item) => item.clip_id === clipId ? [first, second] : [item])
	};
}
