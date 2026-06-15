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
				customVoiceSourceAudioPath: '/tmp/original-source.wav',
				customVoiceSourceDurationMs: 300000,
				customVoiceTrimStartMs: 12000,
				customVoiceTrimEndMs: 18000,
				customVoiceTranscript: '这是自定义音色对应的参考台词。',
				customVoiceConfirmed: false
			}));

			const request = store.toRequest();

			expect(request.engine_id).toBe(engineId);
			expect(request.voice_id).toBeNull();
			expect(request.voice_source).toBe('reference_audio');
			expect(request.reference_audio_path).toBe('/tmp/custom-reference.wav');
			expect(request.ref_text).toBe('这是自定义音色对应的参考台词。');
			expect(request.custom_reference_source_audio_path).toBe('/tmp/original-source.wav');
			expect(request.custom_reference_source_duration_ms).toBe(300000);
			expect(request.custom_reference_trim_start_ms).toBe(12000);
			expect(request.custom_reference_trim_end_ms).toBe(18000);
			expect(request.reference_audio_license_status).toBe('self_voice');
			expect(request.reference_audio_tags).toEqual(['custom-reference']);
		}
	});

	it('preserves video localization context across request restore', () => {
		const store = createGenerateStore();

		store.fromRequest({
			text: '一九九二年，这件事，改变了一切。',
			engine_id: 'indextts-v2',
			source: 'video_localization',
			project_id: 'project-1',
			segment_id: 'cue_0001',
			reference_audio_path: '/tmp/reference.wav',
			ref_text: 'In 1992, this changed everything.',
			language: 'zh',
			emotion_mode: 'follow_reference',
			nfe_step: 32,
			cfg_strength: 2,
			target_rms: 0.1,
			cross_fade_duration: 0.15,
			sway_sampling_coef: -1,
			fix_duration: 0,
			remove_silence: false,
			emo_alpha: 0.6,
			speed: 1,
			temperature: 0.8,
			top_p: 0.8,
			top_k: 30,
			repetition_penalty: 10,
			max_mel_tokens: 1500,
			max_text_tokens_per_segment: 120,
			interval_silence: 200,
			segment_overlap_ms: 50,
			diffusion_steps: 25,
			cfg_rate: 0.7,
			guidance_scale: 2,
			duration: 0,
			output_format: 'wav'
		});

		const request = store.toRequest();

		expect(request.source).toBe('video_localization');
		expect(request.project_id).toBe('project-1');
		expect(request.segment_id).toBe('cue_0001');
	});
});
