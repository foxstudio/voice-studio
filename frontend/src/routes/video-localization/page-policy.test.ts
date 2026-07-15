import { describe, expect, it } from 'vitest';
import type { VideoLocalizationCue, VideoLocalizationQualityIssue } from '$lib/api/types';
import {
	DEFAULT_ASR_ENGINE_ID,
	asrSelectionRequiresUploadConfirmation,
	isDubbingInspectorSection,
	protectCueManualEdit,
	qualityIssueAppliesToStage
} from './+page.svelte';

function issue(code: string): VideoLocalizationQualityIssue {
	return { code, message: code, severity: 'blocker', cue_id: null, speaker_id: null, reference_clip_id: null };
}

function cue(overrides: Partial<VideoLocalizationCue> = {}): VideoLocalizationCue {
	return {
		cue_id: 'cue_1',
		speaker_id: null,
		start_ms: 1000,
		end_ms: 2400,
		audio_route: 'manual_review',
		en_subtitle_text: 'Original text',
		zh_localized_subtitle_text: null,
		tts_recommended_text: null,
		reference_clip_id: null,
		tts_result_id: null,
		tts_audio_path: null,
		tts_batch_task_id: null,
		tts_batch_status: null,
		tts_batch_error: null,
		tts_attempted_at: null,
		source_duration_ms: 1400,
		generated_duration_ms: null,
		source_word_ids: ['word_1', 'word_2'],
		source_text_raw: 'Original text',
		timing_confidence: 'high',
		transcription_revision_id: 'revision_1',
		review_status: 'needs_review',
		quality_flags: ['generated_by_asr'],
		notes: null,
		...overrides
	};
}

describe('video localization ASR engine policy', () => {
	it('defaults to local Qwen3 ASR', () => {
		expect(DEFAULT_ASR_ENGINE_ID).toBe('qwen3-asr-mlx');
	});

	it('requires upload confirmation only for MiMo cloud ASR', () => {
		expect(asrSelectionRequiresUploadConfirmation('qwen3-asr-mlx')).toBe(false);
		expect(asrSelectionRequiresUploadConfirmation('faster-whisper-turbo')).toBe(false);
		expect(asrSelectionRequiresUploadConfirmation('mimo-v2.5-asr')).toBe(true);
	});
});

describe('stage-aware quality issues', () => {
	it('treats only the voice and generate inspectors as the dubbing stage', () => {
		expect(isDubbingInspectorSection('subtitle')).toBe(false);
		expect(isDubbingInspectorSection('style')).toBe(false);
		expect(isDubbingInspectorSection('voice')).toBe(true);
		expect(isDubbingInspectorSection('generate')).toBe(true);
	});

	it('keeps ASR issues visible without treating TTS as a subtitle dependency', () => {
		expect(qualityIssueAppliesToStage(issue('ASR_ALIGNMENT_FAILED'), false, false)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), false, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('REFERENCE_CLIP_MISSING'), false, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), false, false)).toBe(false);
	});

	it('reveals localization and dubbing checks only after those stages begin', () => {
		expect(qualityIssueAppliesToStage(issue('ZH_SUBTITLE_MISSING'), true, false)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), true, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('CUE_SPEAKER_MISSING'), true, true)).toBe(true);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), true, false)).toBe(false);
		expect(qualityIssueAppliesToStage(issue('TTS_TEXT_MISSING'), true, true)).toBe(true);
	});
});

describe('manual cue edit protection', () => {
	it('preserves ASR provenance and protects a manual source-text correction', () => {
		const previous = cue();
		const unchanged = protectCueManualEdit(previous, { ...previous }, { text: true });
		expect(unchanged).toEqual(previous);

		const edited = protectCueManualEdit(previous, { ...previous, en_subtitle_text: 'Human correction' }, { text: true });
		expect(edited.source_word_ids).toEqual(['word_1', 'word_2']);
		expect(edited.transcription_revision_id).toBe('revision_1');
		expect(edited.timing_confidence).toBe('high');
		expect(edited.quality_flags).toContain('generated_by_asr');
		expect(edited.quality_flags).toEqual(expect.arrayContaining([
			'manual_text_edit',
			'protected_manual_edit'
		]));
		expect(edited.quality_flags).not.toContain('timing_review_required');
	});

	it('preserves source provenance but requires review after a manual timeline move', () => {
		const previous = cue();
		const edited = protectCueManualEdit(previous, { ...previous, start_ms: 1100, end_ms: 2500 }, { timing: true });
		expect(edited.source_word_ids).toEqual(['word_1', 'word_2']);
		expect(edited.transcription_revision_id).toBe('revision_1');
		expect(edited.timing_confidence).toBe('low');
		expect(edited.quality_flags).toContain('generated_by_asr');
		expect(edited.quality_flags).toEqual(expect.arrayContaining([
			'manual_timing_edit',
			'protected_manual_edit',
			'timing_review_required'
		]));
	});
});
