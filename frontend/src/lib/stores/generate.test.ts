import { describe, expect, it } from 'vitest';
import { createGenerateStore, REFERENCE_VOICE_ENGINE_IDS } from './generate';
import type { EngineDetail } from '$lib/api/types';

function engineDetail(engineId: string): EngineDetail {
	return {
		manifest: {
			engine_id: engineId,
			display_name: engineId,
			engine_type: engineId.startsWith('mimo-') ? 'cloud' : 'local',
			provider: 'test',
			version: 'test',
			description: '',
			supported_languages: ['zh', 'en'],
			capabilities: ['text_to_speech'],
			sample_rate: null,
			max_tokens: null,
			privacy_level: 'local',
			default_use_case: '',
			parameter_schema: []
		},
		state: {
			engine_id: engineId,
			status: 'loaded',
			model_path: null,
			error_message: null,
			loaded_at: null
		}
	};
}

describe('generate store custom reference voice requests', () => {
	it('sends custom audio and transcript for every reference voice engine', () => {
		for (const engineId of REFERENCE_VOICE_ENGINE_IDS) {
			const store = createGenerateStore();

			store.update((state) => ({
				...state,
				engines: [engineDetail(engineId)],
				engineId,
				text: '需要合成的文本',
				voiceSource: 'reference_audio',
				voiceId: '',
				customVoiceReferenceAudioPath: '/tmp/custom-reference.wav',
				customVoiceTranscript: '这是自定义音色对应的参考台词。',
				customVoiceConfirmed: false
			}));

			const request = store.toRequest();

			expect(request.engine_id).toBe(engineId);
			expect(request.voice_id).toBeNull();
			expect(request.voice_source).toBe('reference_audio');
			expect(request.reference_audio_path).toBe('/tmp/custom-reference.wav');
			expect(request.ref_text).toBe('这是自定义音色对应的参考台词。');
			expect(request.reference_audio_license_status).toBe('self_voice');
			expect(request.reference_audio_tags).toEqual(['custom-reference']);
		}
	});
});
