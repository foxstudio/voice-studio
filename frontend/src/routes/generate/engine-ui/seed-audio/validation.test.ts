import { describe, expect, it } from 'vitest';
import type { EngineInputAsset } from '$lib/api/types';
import { createReferenceAudioDraft } from '../reference-audio/draft';
import {
	createDefaultSeedAudioState,
	setSeedAudioImage,
	setSeedAudioMode,
	setSeedAudioReference,
	updateSeedAudioParameters,
	updateSeedAudioPrompt,
	type SeedAudioReferenceAsset
} from './state';
import { validateSeedAudioEnvelope, validateSeedAudioState } from './validation';

function audioAsset(overrides: { durationMs?: number; sizeBytes?: number; mimeType?: string; license?: string; fileId?: string; path?: string; source?: 'upload' | 'voice_library'; voiceId?: string } = {}): SeedAudioReferenceAsset {
	return {
		assetId: 'audio', type: 'audio', source: overrides.source ?? 'upload', displayName: 'audio.wav', voiceId: overrides.voiceId ?? '', speakerId: '', licenseStatus: overrides.license ?? 'self_voice',
		referenceAudio: createReferenceAudioDraft('audio', {
			source: { fileId: 'source', durationMs: overrides.durationMs ?? 5_000 },
			clip: { fileId: overrides.fileId ?? 'clip', path: overrides.path ?? '', durationMs: overrides.durationMs ?? 5_000, sizeBytes: overrides.sizeBytes ?? 1024, mimeType: overrides.mimeType ?? 'audio/wav' }
		})
	};
}

describe('Seed Audio validation', () => {
	it('validates prompt length and every numeric parameter boundary', () => {
		let state = updateSeedAudioPrompt(createDefaultSeedAudioState(), 'x'.repeat(3001));
		state = updateSeedAudioParameters(state, { speech_rate: 101, loudness_rate: -51, pitch_rate: 13, sample_rate: 12345 as never });
		const codes = validateSeedAudioState(state).errors.map((entry) => entry.code);
		expect(codes).toEqual(expect.arrayContaining(['prompt_too_long', 'sample_rate_unsupported', 'parameter_out_of_range']));
	});

	it('allows empty optional watermark parties because the provider schema allows them', () => {
		let state = updateSeedAudioPrompt(createDefaultSeedAudioState(), '测试');
		state = updateSeedAudioParameters(state, { aigc_metadata: { enable: true, metadata: { content_producer: '', produce_id: '', content_propagator: '', propagate_id: '' } } });
		expect(validateSeedAudioState(state).errors).not.toContainEqual(expect.objectContaining({ code: 'watermark_metadata_missing' }));
	});

	it('checks audio duration, size, format, license and prompt references', () => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		state = setSeedAudioReference(state, 1, audioAsset({ durationMs: 30_001, sizeBytes: 10 * 1024 * 1024 + 1, mimeType: 'audio/flac', license: 'denied' }));
		state = updateSeedAudioPrompt(state, '@音频1 和 @音频3');
		const codes = validateSeedAudioState(state).errors.map((entry) => entry.code);
		expect(codes).toEqual(expect.arrayContaining(['audio_too_long', 'asset_too_large', 'audio_format_unsupported', 'asset_license_denied', 'reference_slot_empty']));
	});

	it('requires exactly the active image input and validates its format', () => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'image');
		state = updateSeedAudioPrompt(state, '图片场景');
		expect(validateSeedAudioState(state).errors[0].code).toBe('image_required');
		state = setSeedAudioImage(state, { assetId: 'image', source: 'upload', fileId: 'file', displayName: 'bad.gif', previewUrl: '', mimeType: 'image/gif', sizeBytes: 100, licenseStatus: 'self_voice' });
		expect(validateSeedAudioState(state).errors).toContainEqual(expect.objectContaining({ code: 'image_format_unsupported' }));
	});

	it('rejects more than three audio assets and any image/audio mix', () => {
		const audio = (id: number): EngineInputAsset => ({ asset_id: String(id), type: 'audio', source: 'upload', file_id: String(id) });
		const assets = [audio(1), audio(2), audio(3), audio(4), { asset_id: 'image', type: 'image', source: 'upload', file_id: 'image' } satisfies EngineInputAsset];
		const codes = validateSeedAudioEnvelope('audio', assets).map((entry) => entry.code);
		expect(codes).toEqual(expect.arrayContaining(['too_many_audio_references', 'mixed_reference_types', 'audio_mode_image_forbidden']));
	});

	it.each(['self_voice', 'authorized', 'company_authorized'])('accepts the backend cloud-upload license %s', (license) => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		state = setSeedAudioReference(state, 1, audioAsset({ license }));
		state = updateSeedAudioPrompt(state, '参考生成');
		expect(validateSeedAudioState(state).errors).toEqual([]);
	});

	it.each(['self_owned', 'test_only', 'unknown', 'denied', ''])('rejects the non-uploadable license %s', (license) => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		state = setSeedAudioReference(state, 1, audioAsset({ license }));
		state = updateSeedAudioPrompt(state, '参考生成');
		expect(validateSeedAudioState(state).errors).toContainEqual(expect.objectContaining({ code: 'asset_license_denied' }));
	});

	it('requires managed file IDs and a voice ID for voice-library references', () => {
		let state = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		state = setSeedAudioReference(state, 1, audioAsset({ fileId: '', path: '/tmp/not-managed.wav' }));
		state = setSeedAudioReference(state, 2, audioAsset({ source: 'voice_library', voiceId: '' }));
		state = updateSeedAudioPrompt(state, '参考生成');
		const codes = validateSeedAudioState(state).errors.map((entry) => entry.code);
		expect(codes).toEqual(expect.arrayContaining(['audio_file_required', 'voice_id_required']));
	});

	it('accepts audio/opus and rejects a restored image without a managed file ID', () => {
		let audioState = setSeedAudioMode(createDefaultSeedAudioState(), 'audio');
		audioState = setSeedAudioReference(audioState, 1, audioAsset({ mimeType: 'audio/opus' }));
		audioState = updateSeedAudioPrompt(audioState, '参考生成');
		expect(validateSeedAudioState(audioState).errors).not.toContainEqual(expect.objectContaining({ code: 'audio_format_unsupported' }));

		let imageState = setSeedAudioMode(createDefaultSeedAudioState(), 'image');
		imageState = setSeedAudioImage(imageState, { assetId: 'image', source: 'upload', fileId: '', displayName: 'lost.png', previewUrl: '', mimeType: 'image/png', sizeBytes: 100, licenseStatus: 'self_voice' });
		imageState = updateSeedAudioPrompt(imageState, '图片场景');
		expect(validateSeedAudioState(imageState).errors).toContainEqual(expect.objectContaining({ code: 'image_file_required' }));
	});

	it('requires exact active-mode asset counts when restoring an envelope', () => {
		expect(validateSeedAudioEnvelope('audio', [])).toContainEqual(expect.objectContaining({ code: 'audio_reference_required' }));
		expect(validateSeedAudioEnvelope('image', [])).toContainEqual(expect.objectContaining({ code: 'image_required' }));
	});
});
