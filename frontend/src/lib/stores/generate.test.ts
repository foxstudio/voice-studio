import { describe, expect, it } from 'vitest';
import { createGenerateStore, REFERENCE_VOICE_ENGINE_IDS } from './generate';
import type { EngineDetail, ParameterSchema } from '$lib/api/types';

function parameter(partial: Partial<ParameterSchema> & Pick<ParameterSchema, 'key' | 'label' | 'type'>): ParameterSchema {
	return {
		default: null,
		description: null,
		min: null,
		max: null,
		step: null,
		options: [],
		required: false,
		capability: null,
		level: 'basic',
		...partial
	};
}

function engineDetail(engineId: string, parameterSchema: EngineDetail['manifest']['parameter_schema'] = []): EngineDetail {
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
			parameter_schema: parameterSchema
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

	it('applies Confucius4 manifest defaults including seed', () => {
		const store = createGenerateStore();

		store.update((state) => ({
			...state,
			engines: [
				engineDetail('confucius4-mlx-int8', [
					parameter({ key: 'language', label: '目标语言', type: 'select', default: 'zh', options: [{ label: '中文', value: 'zh' }] }),
					parameter({ key: 'temperature', label: '随机性', type: 'slider', default: 0.8, min: 0.1, max: 1.5, step: 0.05, level: 'advanced' }),
					parameter({ key: 'top_p', label: 'Top-P', type: 'slider', default: 0.8, min: 0.01, max: 1, step: 0.01, level: 'advanced' }),
					parameter({ key: 'top_k', label: 'Top-K', type: 'slider', default: 30, min: 1, max: 100, step: 1, level: 'advanced' }),
					parameter({ key: 'repetition_penalty', label: '重复惩罚', type: 'slider', default: 10, min: 1, max: 20, step: 0.5, level: 'advanced' }),
					parameter({ key: 'diffusion_steps', label: '声学采样步数', type: 'slider', default: 25, min: 1, max: 60, step: 1, level: 'advanced' }),
					parameter({ key: 'cfg_rate', label: '声学引导强度', type: 'slider', default: 0.7, min: 0, max: 1, step: 0.05, level: 'advanced' }),
					parameter({ key: 'seed', label: '随机种子', type: 'number', default: 0, min: 0, max: 2147483647, step: 1, level: 'developer' })
				])
			]
		}));

		store.setEngine('confucius4-mlx-int8');
		const request = store.toRequest();

		expect(request.engine_id).toBe('confucius4-mlx-int8');
		expect(request.language).toBe('zh');
		expect(request.temperature).toBe(0.8);
		expect(request.top_p).toBe(0.8);
		expect(request.top_k).toBe(30);
		expect(request.repetition_penalty).toBe(10);
		expect(request.diffusion_steps).toBe(25);
		expect(request.cfg_rate).toBe(0.7);
		expect(request.seed).toBe(0);
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
