import { describe, expect, it } from 'vitest';
import { createGenerateStore, REFERENCE_VOICE_ENGINE_IDS } from './generate';
import type { EngineDetail, ParameterSchema, VoiceAsset } from '$lib/api/types';

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

function voiceAsset(partial: Partial<VoiceAsset> = {}): VoiceAsset {
	return {
		name: '本地参考音色',
		voice_type: 'test_sample',
		description: '',
		default_language: 'zh',
		tags: [],
		reference_text: '这是一段库内参考台词。',
		recommended_engine_id: 'qwen3-tts-mlx-0.6b',
		reference_audio_ids: ['ref-audio-1'],
		license_status: 'self_voice',
		voice_id: 'voice-1',
		quality_status: 'unchecked',
		quality_notes: '',
		favorite: false,
		emotion_tags: [],
		created_at: '',
		updated_at: '',
		last_used_at: null,
		engine_bindings: [],
		...partial
	};
}

function qwen3Schema(): ParameterSchema[] {
	return [
		parameter({ key: 'speaker_id', label: '预置音色', type: 'select', default: 'Vivian', options: [{ label: 'Vivian', value: 'Vivian' }] }),
		parameter({ key: 'voice_design_prompt', label: '声音描述', type: 'textarea', default: '' }),
		parameter({ key: 'style_instruction', label: '风格指令', type: 'textarea', default: '' }),
		parameter({ key: 'temperature', label: 'Temperature', type: 'slider', default: 0.7, level: 'advanced' }),
		parameter({ key: 'top_p', label: 'Top-P', type: 'slider', default: 0.9, level: 'advanced' }),
		parameter({ key: 'top_k', label: 'Top-K', type: 'number', default: 50, level: 'advanced' }),
		parameter({ key: 'repetition_penalty', label: '重复惩罚', type: 'number', default: 1.1, level: 'advanced' }),
		parameter({ key: 'cfg_scale', label: 'CFG Scale', type: 'number', default: 1.5, level: 'advanced' })
	];
}

describe('generate store custom reference voice requests', () => {
	it('keeps model UI drafts isolated and intact while switching engines', () => {
		const store = createGenerateStore();
		const seedDraft = { mode: 'audio', prompt: '@音频1 测试' };
		store.update((state) => ({
			...state,
			engines: [engineDetail('indextts-v2'), engineDetail('doubao-seed-audio-1.0')],
			engineUiStateById: { 'doubao-seed-audio-1.0': seedDraft, 'future-engine': { prompt: '另一个模型' } }
		}));

		store.setEngine('doubao-seed-audio-1.0');
		store.setEngine('indextts-v2');

		const unsubscribe = store.subscribe((value) => {
			expect(value.engineUiStateById['doubao-seed-audio-1.0']).toBe(seedDraft);
			expect(value.engineUiStateById['future-engine']).toEqual({ prompt: '另一个模型' });
		});
		unsubscribe();
	});
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

	it('round-trips the official Doubao TTS audio parameters only when declared', () => {
		const store = createGenerateStore();
		const schema = [
			parameter({ key: 'speaker_id', label: '音色', type: 'select', default: 'speaker-1' }),
			parameter({ key: 'pitch_rate', label: '音调', type: 'slider', default: 0, min: -12, max: 12, step: 1, level: 'advanced' }),
			parameter({ key: 'sample_rate', label: '采样率', type: 'select', default: 24000, level: 'advanced' }),
			parameter({ key: 'bit_rate', label: '码率', type: 'select', default: 128000, level: 'advanced' }),
			parameter({ key: 'loudness_rate', label: '音量', type: 'slider', default: 0, level: 'advanced' }),
			parameter({ key: 'enable_subtitle', label: '时间戳', type: 'toggle', default: false, level: 'advanced' }),
			parameter({ key: 'silence_duration', label: '结尾静音', type: 'number', default: 0, level: 'advanced' }),
			parameter({ key: 'aigc_watermark', label: 'AIGC 标识', type: 'toggle', default: false, level: 'advanced' })
		];
		store.update((state) => ({
			...state,
			engines: [engineDetail('doubao-tts-preset', schema)],
			engineId: 'doubao-tts-preset',
			text: '测试文本',
			pitchRate: 5,
			doubaoSampleRate: 48000,
			doubaoBitRate: 160000,
			doubaoLoudnessRate: 25,
			doubaoEnableSubtitle: true,
			doubaoSilenceDuration: 700,
			doubaoAigcWatermark: true
		}));

		const request = store.toRequest();
		expect(request.pitch_rate).toBe(5);
		expect(request.sample_rate).toBe(48000);
		expect(request.bit_rate).toBe(160000);
		expect(request.loudness_rate).toBe(25);
		expect(request.enable_subtitle).toBe(true);
		expect(request.silence_duration).toBe(700);
		expect(request.aigc_watermark).toBe(true);

		store.fromRequest({ ...request, pitch_rate: -4, sample_rate: 16000, bit_rate: 96000, loudness_rate: -20, enable_subtitle: false, silence_duration: 200, aigc_watermark: false });
		const unsubscribe = store.subscribe((value) => {
			expect(value.pitchRate).toBe(-4);
			expect(value.doubaoSampleRate).toBe(16000);
			expect(value.doubaoBitRate).toBe(96000);
			expect(value.doubaoLoudnessRate).toBe(-20);
			expect(value.doubaoEnableSubtitle).toBe(false);
			expect(value.doubaoSilenceDuration).toBe(200);
			expect(value.doubaoAigcWatermark).toBe(false);
		});
		unsubscribe();
	});

	it('does not add Doubao pitch rate to unrelated engine requests', () => {
		const store = createGenerateStore();
		store.update((state) => ({
			...state,
			engines: [engineDetail('indextts-v2')],
			engineId: 'indextts-v2',
			pitchRate: 7
		}));

		expect(store.toRequest().pitch_rate).toBeUndefined();
	});

	it('keeps Qwen3 preset, voice design, library, and custom voice routes mutually exclusive', () => {
		const store = createGenerateStore();

		store.update((state) => ({
			...state,
			engines: [engineDetail('qwen3-tts-mlx-0.6b', qwen3Schema())],
			voices: [voiceAsset()],
			engineId: 'qwen3-tts-mlx-0.6b',
			text: '需要合成的文本'
		}));
		store.setEngine('qwen3-tts-mlx-0.6b');

		store.update((state) => ({
			...state,
			voiceSource: 'voice_library',
			voiceId: 'voice-1',
			speakerId: 'Vivian',
			voiceDesignPrompt: '温柔的中文女声',
			styleInstruction: '语速稍慢'
		}));
		const libraryRequest = store.toRequest();
		expect(libraryRequest.voice_id).toBe('voice-1');
		expect(libraryRequest.ref_text).toBe('这是一段库内参考台词。');
		expect(libraryRequest.reference_audio_path).toBeNull();
		expect(libraryRequest.speaker_id).toBeNull();
		expect(libraryRequest.voice_design_prompt).toBeNull();
		expect(libraryRequest.style_instruction).toBeNull();

		store.update((state) => ({
			...state,
			voiceSource: 'reference_audio',
			voiceId: '',
			customVoiceReferenceAudioPath: '/tmp/custom-reference.wav',
			customVoiceTranscript: '自定义参考台词。',
			speakerId: 'Vivian',
			voiceDesignPrompt: '温柔的中文女声',
			styleInstruction: '语速稍慢'
		}));
		const customRequest = store.toRequest();
		expect(customRequest.voice_id).toBeNull();
		expect(customRequest.reference_audio_path).toBe('/tmp/custom-reference.wav');
		expect(customRequest.ref_text).toBe('自定义参考台词。');
		expect(customRequest.speaker_id).toBeNull();
		expect(customRequest.voice_design_prompt).toBeNull();
		expect(customRequest.style_instruction).toBeNull();

		store.update((state) => ({
			...state,
			voiceSource: 'voice_library',
			voiceId: '',
			customVoiceReferenceAudioPath: '',
			customVoiceTranscript: '',
			speakerId: 'Vivian',
			voiceDesignPrompt: '',
			styleInstruction: '语速稍慢'
		}));
		const presetRequest = store.toRequest();
		expect(presetRequest.voice_id).toBeNull();
		expect(presetRequest.reference_audio_path).toBeNull();
		expect(presetRequest.speaker_id).toBe('Vivian');
		expect(presetRequest.style_instruction).toBe('语速稍慢');

		store.update((state) => ({ ...state, voiceDesignPrompt: '年轻中文女声，吐字清晰' }));
		const designRequest = store.toRequest();
		expect(designRequest.speaker_id).toBeNull();
		expect(designRequest.voice_design_prompt).toBe('年轻中文女声，吐字清晰');
		expect(designRequest.style_instruction).toBeNull();
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
