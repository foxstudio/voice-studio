import type {
	VideoLocalizationDraft,
	VideoLocalizationDubSubtitleReviewRequest
} from '$lib/api/types';

export type DubSubtitleReviewPatch = {
	text?: string;
	start_ms?: number;
	end_ms?: number;
};

export function buildDubSubtitleReviewRequest(
	draft: VideoLocalizationDraft,
	subtitleId: string,
	patch: DubSubtitleReviewPatch
): VideoLocalizationDubSubtitleReviewRequest {
	const sourceRevision = draft.dub_subtitle_source_revision?.trim();
	if (!sourceRevision) throw new Error('配音字幕版本不可用，请重新生成或刷新后再试');
	const subtitles = draft.dub_subtitles ?? [];
	const selected = subtitles.find((cue) => cue.subtitle_id === subtitleId);
	if (!selected) throw new Error('没有找到要编辑的配音字幕，请刷新后再试');
	const text = (patch.text ?? selected.text).trim();
	if (!text) throw new Error('配音字幕不能为空');
	const startMs = Math.round(patch.start_ms ?? selected.start_ms);
	const endMs = Math.round(patch.end_ms ?? selected.end_ms);
	if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) throw new Error('配音字幕时间码无效');
	if (startMs < 0) throw new Error('配音字幕入点不能早于视频开头');
	if (endMs <= startMs) throw new Error('配音字幕出点必须晚于入点');
	const durationMs = Math.max(0, Math.round(draft.source_media.duration_ms ?? 0));
	if (durationMs && endMs > durationMs) throw new Error('配音字幕出点不能晚于视频结尾');
	return {
		source_revision: sourceRevision,
		cues: subtitles.map((cue) => ({
			subtitle_id: cue.subtitle_id,
			source_subtitle_ids: [cue.subtitle_id],
			start_ms: cue.subtitle_id === subtitleId ? startMs : cue.start_ms,
			end_ms: cue.subtitle_id === subtitleId ? endMs : cue.end_ms,
			text: cue.subtitle_id === subtitleId ? text : cue.text
		}))
	};
}
