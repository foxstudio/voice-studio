import type { VideoLocalizationTimelineClip } from '$lib/api/types';
import { resolveDubClipLane } from './dub-track-lanes';

export type TtsInitializationPlaceholderInput = {
	clientId: string;
	segmentId: string;
	primaryCueId: string | null;
	sourceCueIds: string[];
	startMs: number;
	endMs: number;
	targetClip?: VideoLocalizationTimelineClip | null;
};

export type TtsInitializationClipCleanup = {
	clips: VideoLocalizationTimelineClip[];
	removedClipIds: string[];
};

export function ttsInitializationFailureMessage(error: unknown) {
	const message = error instanceof Error ? error.message.trim() : String(error ?? '').trim();
	if (
		/failed to fetch/i.test(message)
		|| /load failed/i.test(message)
		|| /networkerror/i.test(message)
		|| /network request failed/i.test(message)
	) {
		return '接口当前无法连接，请等待服务恢复后重试';
	}
	return message || '提交配音生成失败';
}

/**
 * Creates a client-only placeholder for a new take without borrowing or
 * removing the selected take's identity. Overlap is resolved as a lane
 * placement concern, so every existing take remains visible and playable.
 */
export function createTtsInitializationPlaceholder(
	clips: readonly VideoLocalizationTimelineClip[],
	input: TtsInitializationPlaceholderInput
): VideoLocalizationTimelineClip {
	const target = input.targetClip ?? null;
	const startMs = Math.max(0, target?.start_ms ?? input.startMs);
	const endMs = Math.max(startMs + 300, target?.end_ms ?? input.endMs);
	const preferredLane = Number(target?.dub_lane ?? 0);
	return {
		clip_id: `pending_tts_init_${input.clientId}`,
		track_id: 'dub',
		subtitle_id: input.segmentId,
		cue_id: input.primaryCueId,
		source_cue_ids: [...input.sourceCueIds],
		start_ms: startMs,
		end_ms: endMs,
		source_start_ms: 0,
		source_end_ms: endMs - startMs,
		audio_path: null,
		dub_lane: resolveDubClipLane([...clips], startMs, endMs, preferredLane),
		status: 'queued',
		status_label: '准备提交',
		generation_progress: 0,
		optimistic_tts_workflow_id: `init:${input.clientId}`
	};
}

export function promoteTtsInitializationPlaceholder(
	clips: readonly VideoLocalizationTimelineClip[],
	clientId: string,
	workflowMarker: string
) {
	const initializationMarker = `init:${clientId}`;
	return clips.map((clip) =>
		clip.optimistic_tts_workflow_id === initializationMarker
			? {
					...clip,
					status: 'queued',
					status_label: '排队中',
					optimistic_tts_workflow_id: workflowMarker
				}
			: clip
	);
}

/**
 * Removes a local TTS-initialization placeholder. Durable target clips live in
 * the persisted Draft and therefore never need to be restored from runtime.
 */
export function cleanupTtsInitializationClips(
	clips: readonly VideoLocalizationTimelineClip[],
	clientId: string
): TtsInitializationClipCleanup {
	const marker = `init:${clientId}`;
	const matching = clips.filter((clip) => clip.optimistic_tts_workflow_id === marker);
	const retained = clips.filter((clip) => clip.optimistic_tts_workflow_id !== marker);

	return {
		clips: retained,
		removedClipIds: matching.map((clip) => clip.clip_id)
	};
}
