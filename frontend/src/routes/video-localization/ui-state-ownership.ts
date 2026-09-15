import { mergeVideoLocalizationUiState } from './ui-state-merge';

const CLIENT_UI_STATE_FIELDS = new Set([
	'audio_track_order',
	'disabled_media_tracks',
	'dub_lane_states',
	'inspector_width',
	'playhead_ms',
	'selected_cue_id',
	'sidebar_collapsed',
	'subtitle_display_mode',
	'subtitle_preview',
	'subtitle_workflow_settings_open',
	'timeline_hover_scrub_enabled',
	'timeline_viewport_start_ms',
	'timeline_zoom',
	'track_states'
]);

export function clientVideoLocalizationUiPatch(patch: Record<string, unknown>) {
	return Object.fromEntries(
		Object.entries(patch).filter(([key]) => CLIENT_UI_STATE_FIELDS.has(key))
	);
}

export function mergeVideoLocalizationUiStateAfterConflict(
	latest: Record<string, unknown>,
	local: Record<string, unknown>
) {
	const clientPatch = clientVideoLocalizationUiPatch(local);
	const merged = mergeVideoLocalizationUiState(latest, clientPatch);
	const latestDiscarded = Array.isArray(latest.discarded_tts_task_ids)
		? latest.discarded_tts_task_ids.map(String)
		: [];
	const localDiscarded = Array.isArray(local.discarded_tts_task_ids)
		? local.discarded_tts_task_ids.map(String)
		: [];
	if (
		latestDiscarded.length
		|| localDiscarded.length
		|| Object.prototype.hasOwnProperty.call(latest, 'discarded_tts_task_ids')
		|| Object.prototype.hasOwnProperty.call(local, 'discarded_tts_task_ids')
	) {
		merged.discarded_tts_task_ids = [...new Set([...latestDiscarded, ...localDiscarded])];
	}
	return merged;
}
