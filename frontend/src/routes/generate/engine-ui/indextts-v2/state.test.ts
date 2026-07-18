import { describe, expect, it } from 'vitest';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import { createDefaultIndexTtsEmotionState, setEmotionReferenceSource, setEmotionReferenceSlot } from './state';
import { indexTtsEmotionStateFromRequest, indexTtsEmotionStateToRequest } from './request';
import { validateIndexTtsEmotionState } from './validation';

function readyDraft() {
	return createReferenceAudioDraft('emotion-1', {
		sourceKind: 'upload',
		source: { path: '/voices/source.wav', durationMs: 10_000 },
		clip: { path: '/voices/clip.wav', durationMs: 6_000 },
		trim: { startMs: 2_000, endMs: 8_000 },
		confirmed: true
	});
}

describe('IndexTTS independent emotion state', () => {
	it('keeps the legacy follow-reference request clean while disabled', () => {
		const request = indexTtsEmotionStateToRequest(createDefaultIndexTtsEmotionState(), 'follow_reference', 0.6);
		expect(request).toEqual({ emotion_mode: 'follow_reference', emo_alpha: 0.6 });
	});

	it('blocks an enabled reference until its selected range has been applied', () => {
		let state = createDefaultIndexTtsEmotionState();
		state = setEmotionReferenceSlot(state, 'voice_library', {
			voiceId: 'voice-a', audioId: 'audio-a', displayName: '待处理样音',
			draft: createReferenceAudioDraft('pending', { source: { fileId: 'audio-a', durationMs: 8_000 }, trim: { startMs: 0, endMs: 8_000 }, selectionDirty: true })
		});
		state = { ...state, enabled: true };
		expect(validateIndexTtsEmotionState(state)).toMatchObject({ valid: false });
		expect(validateIndexTtsEmotionState(state).errors[0]).toContain('使用这个片段');
	});

	it('submits only the active library draft and keeps both source drafts', () => {
		let state = createDefaultIndexTtsEmotionState();
		state = setEmotionReferenceSlot(state, 'voice_library', { voiceId: 'voice-a', audioId: 'audio-a', displayName: '悲伤样音', draft: readyDraft() });
		state = setEmotionReferenceSlot(state, 'upload', { voiceId: '', audioId: 'upload-a', displayName: '上传样音', draft: readyDraft() });
		state = { ...setEmotionReferenceSource(state, 'voice_library'), enabled: true, alpha: 0.75 };
		const request = indexTtsEmotionStateToRequest(state, 'follow_reference', 0.6);
		expect(request).toMatchObject({ emotion_mode: 'emotion_reference', emo_alpha: 0.75, emotion_reference_voice_id: 'voice-a', emotion_reference_audio_path: '/voices/clip.wav' });
		expect(state.upload.displayName).toBe('上传样音');
		expect(validateIndexTtsEmotionState(state).valid).toBe(true);
	});

	it('restores independent emotion history without enabling old requests', () => {
		const restored = indexTtsEmotionStateFromRequest({
			emotion_mode: 'emotion_reference', emo_alpha: 0.5, emotion_reference_voice_id: 'voice-a',
			emotion_reference_audio_path: '/voices/clip.wav', emotion_reference_source_audio_path: '/voices/source.wav',
			emotion_reference_source_duration_ms: 12_000, emotion_reference_trim_start_ms: 1_000, emotion_reference_trim_end_ms: 7_000
		});
		expect(restored.enabled).toBe(true);
		expect(restored.source).toBe('voice_library');
		expect(restored.library.draft?.trim).toEqual({ startMs: 1_000, endMs: 7_000 });
		expect(restored.library.draft?.source.fileId).toBe('source');
		expect(restored.library.draft?.source.previewUrl).toBe('/api/voices/files/source/audio');
		expect(restored.library.draft?.clip.fileId).toBe('clip');
		expect(restored.library.draft?.clip.previewUrl).toBe('/api/voices/files/clip/audio');
		expect(indexTtsEmotionStateFromRequest({ emotion_mode: 'follow_reference' }).enabled).toBe(false);
	});

	it('preserves the built-in emotion mode when independent reference stays disabled', () => {
		const restored = indexTtsEmotionStateFromRequest({ emotion_mode: 'emotion_vector', emotion: 'happy', emo_alpha: 0.8 });
		const request = indexTtsEmotionStateToRequest(restored, 'emotion_vector', 0.8);
		expect(request).toEqual({ emotion_mode: 'emotion_vector', emo_alpha: 0.8 });
	});
});
