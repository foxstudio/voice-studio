import { describe, expect, it } from 'vitest';
import type { VideoLocalizationDraft } from '$lib/api/types';
import { withoutSubtitleTrack } from './subtitle-track-clear';

function draftFixture(): VideoLocalizationDraft {
	return {
		project_type: 'video_localization', schema_version: 'v1', status: 'draft',
		source_media: {
			filename: null, duration_ms: null, video_path: null, audio_path: null, size_bytes: null,
			width: null, height: null, frame_rate: null, imported_at: null,
			metadata: { english_asr_engine_id: 'qwen3-asr-mlx', keep_me: 'yes' }
		},
		stems: {
			vocals_clean_path: null, background_path: null, original_audio_path: null,
			separation_engine_id: null, separation_status: 'pending', quality_flags: [], analysis_version: 'v1'
		},
		speakers: [], reference_clips: [],
		cues: [{
			cue_id: 'cue_0001', speaker_id: null, start_ms: 0, end_ms: 1000,
			audio_route: 'clone_from_source', en_subtitle_text: 'Source', zh_localized_subtitle_text: '本土化',
			tts_recommended_text: null, reference_clip_id: null, tts_result_id: null, tts_audio_path: null,
			tts_batch_task_id: null, tts_batch_status: null, tts_batch_error: null, tts_attempted_at: null,
			source_duration_ms: 1000, generated_duration_ms: null, source_word_ids: [], source_text_raw: 'Source',
			timing_confidence: 'high', transcription_revision_id: 'rev', review_status: 'ready', quality_flags: [], notes: null
		}],
		transcription: {} as VideoLocalizationDraft['transcription'],
		localized_subtitles: [{ subtitle_id: 'subtitle_0001', start_ms: 0, end_ms: 1000, text: '本土化', linked_cue_id: 'cue_0001', quality_flags: [] }],
		quality_gate: {} as VideoLocalizationDraft['quality_gate'],
		exports: {} as VideoLocalizationDraft['exports'],
		operations: [], glossary: [], scene_context: '', ui_state: { selected_cue_id: 'cue_0001' },
		project_voice_samples: [], voice_recipes: [], generated_candidates: [], timeline_clips: [], updated_at: null
	};
}

describe('withoutSubtitleTrack', () => {
	it('clears ASR state immediately while preserving the localized track', () => {
		const result = withoutSubtitleTrack(draftFixture(), 'asr');

		expect(result.cues).toEqual([]);
		expect(result.transcription).toBeNull();
		expect(result.source_media.metadata).toEqual({ keep_me: 'yes' });
		expect(result.localized_subtitles[0].linked_cue_id).toBeNull();
		expect(result.ui_state.selected_cue_id).toBe('');
	});

	it('clears only the localized subtitle track', () => {
		const source = draftFixture();
		const result = withoutSubtitleTrack(source, 'localized');

		expect(result.localized_subtitles).toEqual([]);
		expect(result.cues).toEqual(source.cues);
		expect(result.transcription).toBe(source.transcription);
	});
});
