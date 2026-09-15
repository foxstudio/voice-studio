import type { VideoLocalizationDraft } from '$lib/api/types';

export type ClearableSubtitleTrack = 'asr' | 'localized';

export function withoutSubtitleTrack(
	draft: VideoLocalizationDraft,
	track: ClearableSubtitleTrack
): VideoLocalizationDraft {
	if (track === 'localized') {
		return { ...draft, localized_subtitles: [] };
	}

	const metadata = Object.fromEntries(
		Object.entries(draft.source_media.metadata).filter(([key]) => !key.startsWith('english_asr_'))
	);
	return {
		...draft,
		source_media: { ...draft.source_media, metadata },
		cues: [],
		transcription: null,
		localized_subtitles: draft.localized_subtitles.map((subtitle) => ({
			...subtitle,
			linked_cue_id: null
		})),
		ui_state: { ...draft.ui_state, selected_cue_id: '' }
	};
}
