import type { HistoryItem, VideoLocalizationTimelineClip } from '$lib/api/types';

type AudioIdentity = {
	result_id?: unknown;
	generation_identity?: unknown;
	generation_id?: unknown;
	task_id?: unknown;
};

function nonEmptyString(value: unknown) {
	return typeof value === 'string' && value.trim() ? value.trim() : '';
}

export function shortAudioIdentity(item: AudioIdentity) {
	const identity = [
		item.result_id,
		item.generation_identity,
		item.generation_id,
		item.task_id
	].map(nonEmptyString).find(Boolean) ?? '';
	const readableIdentity = identity.replace(/^(?:result|generation|task)[_-]/i, '');
	return readableIdentity ? readableIdentity.slice(0, 6) : '';
}

export function historyCueIds(item: HistoryItem) {
	const parameterCueIds = item.parameter_snapshot?.video_localization_source_cue_ids;
	return [...new Set([
		...(Array.isArray(parameterCueIds) ? parameterCueIds.map(nonEmptyString) : []),
		nonEmptyString(item.cue_id)
	].filter(Boolean))];
}

export function historyAudioIdentityLabel(item: HistoryItem) {
	const cueLabel = historyCueIds(item).join(' + ')
		|| nonEmptyString(item.segment_id)
		|| nonEmptyString(item.localized_subtitle_id)
		|| '未关联片段';
	const audioIdentity = shortAudioIdentity(item);
	return audioIdentity ? `${cueLabel} · 音频 ${audioIdentity}` : cueLabel;
}

function clipSourceIdentity(clip: VideoLocalizationTimelineClip) {
	return nonEmptyString(clip.media_source_clip_id) || nonEmptyString(clip.clip_id);
}

export function timelineDubClipLabel(
	clip: VideoLocalizationTimelineClip,
	clips: VideoLocalizationTimelineClip[] = [clip]
) {
	const cueLabel = nonEmptyString(clip.status_label)
		|| nonEmptyString(clip.cue_id)
		|| nonEmptyString(clip.clip_id);
	const audioIdentity = shortAudioIdentity(clip);
	const sourceIdentity = clipSourceIdentity(clip);
	const siblings = clips
		.filter((candidate) => (
			candidate.track_id === 'dub'
			&& clipSourceIdentity(candidate) === sourceIdentity
			&& shortAudioIdentity(candidate) === audioIdentity
		))
		.sort((left, right) => (
			Number(left.source_start_ms ?? left.start_ms ?? 0)
			- Number(right.source_start_ms ?? right.start_ms ?? 0)
		));
	const sliceIndex = siblings.findIndex((candidate) => candidate.clip_id === clip.clip_id);
	const sliceLabel = siblings.length > 1 && sliceIndex >= 0
		? ` · 切片 ${sliceIndex + 1}/${siblings.length}`
		: '';
	return `${audioIdentity ? `${cueLabel} · 音频 ${audioIdentity}` : cueLabel}${sliceLabel}`;
}
