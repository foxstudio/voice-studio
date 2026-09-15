export const VIDEO_LOCALIZATION_TTS_HANDOFF_INTENT_KEY = 'voice-studio-video-localization-handoff-intent';
export const VIDEO_LOCALIZATION_TTS_HANDOFF_META_KEY = 'voice-studio-video-localization-handoff';
export const VIDEO_LOCALIZATION_TTS_HANDOFF_REQUEST_KEY = 'voice-studio-history-reuse';

export type VideoLocalizationTtsHandoffIntent = {
	schema_version: 'video-localization-tts-handoff-v1';
	source: 'video_localization';
	mode: 'reference_only';
	project_id: string;
	segment_id: string;
	subtitle_id: string | null;
	target_subtitle_ids: string[];
	source_cue_ids: string[];
	created_at: string;
};

export function parseVideoLocalizationTtsHandoffIntent(raw: string | null) {
	if (!raw) return null;
	try {
		const value = JSON.parse(raw) as Partial<VideoLocalizationTtsHandoffIntent>;
		if (
			value.schema_version !== 'video-localization-tts-handoff-v1'
			|| value.source !== 'video_localization'
			|| value.mode !== 'reference_only'
			|| !value.project_id
			|| !value.segment_id
			|| !Array.isArray(value.target_subtitle_ids)
			|| !value.target_subtitle_ids.length
			|| !Array.isArray(value.source_cue_ids)
		) return null;
		return {
			schema_version: value.schema_version,
			source: value.source,
			mode: value.mode,
			project_id: value.project_id,
			segment_id: value.segment_id,
			subtitle_id: value.subtitle_id ?? null,
			target_subtitle_ids: [...new Set(value.target_subtitle_ids.map(String).filter(Boolean))],
			source_cue_ids: [...new Set(value.source_cue_ids.map(String).filter(Boolean))],
			created_at: value.created_at || new Date().toISOString()
		} satisfies VideoLocalizationTtsHandoffIntent;
	} catch {
		return null;
	}
}
