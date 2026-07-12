import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import type { EngineInputAsset } from '$lib/api/types';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import { applySeedAudioPreset, BUILT_IN_SEED_AUDIO_PRESETS, seedAudioPresetsForMode, type SeedAudioPresetBundle } from './presets';
import { seedAudioParametersToRequest, seedAudioStateFromRequest, seedAudioStateToRequest, type SeedAudioRequestEnvelope } from './request';
import {
	SEED_AUDIO_ENGINE_ID,
	createDefaultSeedAudioParameters,
	createDefaultSeedAudioState,
	setSeedAudioMode,
	setSeedAudioReference,
	updateSeedAudioParameters,
	updateSeedAudioPrompt,
	type SeedAudioReferenceAsset
} from './state';

function audioAsset(id: string, source: 'upload' | 'voice_library' = 'upload'): SeedAudioReferenceAsset {
	return {
		assetId: id, type: 'audio', source, displayName: id, voiceId: source === 'voice_library' ? `voice-${id}` : '', speakerId: '', licenseStatus: 'self_voice',
		referenceAudio: createReferenceAudioDraft(id, {
			source: { fileId: `source-${id}`, durationMs: 10_000 },
			clip: { fileId: `clip-${id}`, durationMs: 4_000, mimeType: 'audio/wav', sizeBytes: 2048 },
			trim: { startMs: 1_000, endMs: 5_000 }, transcript: { text: `${id} 台词` }, confirmed: true
		})
	};
}

function speakerAsset(id: string): SeedAudioReferenceAsset {
	return { assetId: id, type: 'speaker', source: 'cloud_speaker', displayName: id, voiceId: '', speakerId: `speaker-${id}`, licenseStatus: 'authorized', referenceAudio: null };
}

describe('Seed Audio request conversion', () => {
	it('creates a text envelope with official parameter names including all watermark metadata', () => {
		let state = updateSeedAudioPrompt(createDefaultSeedAudioState(), '  清晨的街道声音  ');
		state = updateSeedAudioParameters(state, {
			format: 'ogg_opus', sample_rate: 48000, speech_rate: 12, loudness_rate: -8, pitch_rate: 2,
			aigc_metadata: { enable: true, metadata: { content_producer: '制作方', produce_id: 'p-1', content_propagator: '传播方', propagate_id: 'd-1' } }
		});
		const request = seedAudioStateToRequest(state);

		expect(request).toMatchObject({ engine_id: SEED_AUDIO_ENGINE_ID, input_mode: 'text', text: '清晨的街道声音', input_assets: [] });
		expect(request.engine_parameters).toMatchObject({
			aigc_metadata_enable: true,
			content_producer: '制作方', produce_id: 'p-1', content_propagator: '传播方', propagate_id: 'd-1'
		});
	});

	it('compacts audio slots, rewrites @ references, and preserves upload/library/speaker sources', () => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		state = setSeedAudioReference(state, 1, speakerAsset('narrator'));
		state = setSeedAudioReference(state, 3, audioAsset('rain', 'voice_library'));
		state = updateSeedAudioPrompt(state, '@音频3 作为环境声，@音频1 负责旁白');
		const request = seedAudioStateToRequest(state);

		expect(request.text).toBe('@音频2 作为环境声，@音频1 负责旁白');
		expect(request.input_assets).toHaveLength(2);
		expect(request.input_assets[0]).toMatchObject({ type: 'speaker', source: 'cloud_speaker', speaker_id: 'speaker-narrator' });
		expect(request.input_assets[1]).toMatchObject({ type: 'audio', source: 'voice_library', file_id: 'clip-rain', source_file_id: 'source-rain', ref_text: 'rain 台词' });
	});

	it('round-trips audio envelope and official watermark fields through fromRequest/toRequest', () => {
		const parameters = { format: 'wav', sample_rate: 24000, enable_subtitle: false, speech_rate: 0, loudness_rate: 0, pitch_rate: 0, aigc_watermark: false, aigc_metadata_enable: true, content_producer: 'A', produce_id: 'A1', content_propagator: 'B', propagate_id: 'B1' } as const;
		const request: SeedAudioRequestEnvelope = {
			engine_id: SEED_AUDIO_ENGINE_ID, input_mode: 'audio', text: '@音频1 开场', engine_parameters: parameters,
			input_assets: [{ asset_id: 'speaker', type: 'speaker', source: 'cloud_speaker', speaker_id: 'speaker-1', display_name: '旁白', license_status: null }]
		};
		const result = seedAudioStateToRequest(seedAudioStateFromRequest(request));

		expect(result).toEqual(request);
	});

	it('restores one image without leaking it into audio or text drafts', () => {
		const request: SeedAudioRequestEnvelope = {
			engine_id: SEED_AUDIO_ENGINE_ID, input_mode: 'image', text: '让画面中的人物轻声说话', engine_parameters: { format: 'wav', sample_rate: 24000, enable_subtitle: false, speech_rate: 0, loudness_rate: 0, pitch_rate: 0, aigc_watermark: false, aigc_metadata_enable: false, content_producer: '', produce_id: '', content_propagator: '', propagate_id: '' },
			input_assets: [{ asset_id: 'image', type: 'image', source: 'upload', file_id: 'image-file', display_name: 'scene.webp', mime_type: 'image/webp', size_bytes: 1000 }]
		};
		const state = seedAudioStateFromRequest(request);

		expect(state.drafts.image.image).toMatchObject({ fileId: 'image-file', displayName: 'scene.webp' });
		expect(state.drafts.audio.references.every((slot) => slot.asset === null)).toBe(true);
		expect(state.drafts.text.prompt).toBe('');
	});

	it('rejects invalid state and mixed envelopes before request submission or recovery', () => {
		expect(() => seedAudioStateToRequest(createDefaultSeedAudioState())).toThrow('请输入生成描述');
		const mixed: EngineInputAsset[] = [
			{ asset_id: 'audio', type: 'audio', source: 'upload', file_id: 'a' },
			{ asset_id: 'image', type: 'image', source: 'upload', file_id: 'i' }
		];
		expect(() => seedAudioStateFromRequest({ engine_id: SEED_AUDIO_ENGINE_ID, input_mode: 'audio', text: 'x', input_assets: mixed, engine_parameters: {} })).toThrow('不能同时提交');
	});
});

describe('Seed Audio preset bundles', () => {
	it('filters built-in presets by the current Seed mode without fallback', () => {
		expect(seedAudioPresetsForMode('text')).toHaveLength(7);
		expect(seedAudioPresetsForMode('audio')).toEqual([]);
		expect(seedAudioPresetsForMode('image')).toEqual([]);
	});

	it('contains exactly the seven user-provided reference presets and no legacy examples', () => {
		const expectedIds = [
			'seed-text-mansion-call-v1',
			'seed-text-palace-medicine-trial-v1',
			'seed-text-lingshan-reckoning-v1',
			'seed-text-new-eden-plan-v1',
			'seed-text-friendship-bed-v1',
			'seed-text-disney-podcast-v1',
			'seed-text-durian-livestream-v1'
		];
		const expectedNames = ['男公馆来电', '太后试药', '灵山问罪', '乐土计划', '友情与地板', '最冷一天逛迪士尼', '榴莲直播间'];
		const ids = BUILT_IN_SEED_AUDIO_PRESETS.map((preset) => preset.presetId);
		const names = BUILT_IN_SEED_AUDIO_PRESETS.map((preset) => preset.name);

		expect(ids).toEqual(expectedIds);
		expect(names).toEqual(expectedNames);
		expect(new Set(ids).size).toBe(7);
		expect(new Set(names.map((name) => name.trim().toLocaleLowerCase('zh-CN'))).size).toBe(7);
		expect(ids).not.toContain('seed-text-knowledge-narration-v1');
		expect(ids).not.toContain('seed-text-suspense-dialogue-v1');
		expect(names).not.toContain('知识旁白');
		expect(names).not.toContain('悬疑对话');
	});

	it('ships structurally valid authorized long-form text prompts with content-bound checksums', () => {
		const checksums = new Set<string>();
		for (const preset of BUILT_IN_SEED_AUDIO_PRESETS) {
			const prompt = preset.promptTemplate.trim();
			const digest = createHash('sha256').update(prompt, 'utf8').digest('hex');

			expect(preset.engineId).toBe(SEED_AUDIO_ENGINE_ID);
			expect(preset.inputMode).toBe('text');
			expect(preset.licenseStatus).toBe('authorized');
			expect(preset.assets).toEqual([]);
			expect(preset.promptTemplate).toBe(prompt);
			expect(Array.from(prompt).length).toBeGreaterThanOrEqual(300);
			expect(Array.from(prompt).length).toBeLessThanOrEqual(3000);
			expect(preset.description.trim()).not.toBe('');
			expect(preset.tags.length).toBeGreaterThan(0);
			expect(preset.tags.every((tag) => tag === tag.trim() && tag.length > 0)).toBe(true);
			expect(preset.sourceName).toBe('用户提供参考原文');
			expect(preset.sourceUrl).toBe('');
			expect(preset.version).toMatch(/^\d+\.\d+\.\d+$/);
			expect(preset.engineParameters).toEqual(createDefaultSeedAudioParameters());
			expect(preset.checksum).toBe(`inline-text:sha256:${digest}`);
			checksums.add(preset.checksum);
		}
		expect(checksums.size).toBe(BUILT_IN_SEED_AUDIO_PRESETS.length);
	});

	it('applies every replacement preset as a valid text-only generation request', () => {
		for (const preset of BUILT_IN_SEED_AUDIO_PRESETS) {
			const state = applySeedAudioPreset(createDefaultSeedAudioState(), preset);
			const request = seedAudioStateToRequest(state);

			expect(state.mode).toBe('text');
			expect(state.drafts.text.prompt).toBe(preset.promptTemplate);
			expect(request).toEqual({
				engine_id: SEED_AUDIO_ENGINE_ID,
				text: preset.promptTemplate,
				input_mode: 'text',
				input_assets: [],
				engine_parameters: seedAudioParametersToRequest(preset.engineParameters)
			});
		}
	});

	it('applies one mode without deleting other mode drafts', () => {
		let state = createDefaultSeedAudioState();
		state = updateSeedAudioPrompt(state, '保留的文字草稿');
		const preset: SeedAudioPresetBundle = {
			presetId: 'test-audio', name: '测试参考声音', description: '', engineId: SEED_AUDIO_ENGINE_ID, inputMode: 'audio',
			promptTemplate: '@音频1 测试', assets: [{ asset_id: 'speaker', type: 'speaker', source: 'cloud_speaker', speaker_id: 'speaker-test' }],
			engineParameters: createDefaultSeedAudioParameters(), sourceName: '测试', sourceUrl: '', licenseStatus: 'test_only', version: '1', checksum: 'test:audio', tags: []
		};
		state = applySeedAudioPreset(state, preset);

		expect(state.mode).toBe('audio');
		expect(state.drafts.audio.references[0].asset?.speakerId).toBe('speaker-test');
		expect(state.drafts.text.prompt).toBe('保留的文字草稿');
	});
});
