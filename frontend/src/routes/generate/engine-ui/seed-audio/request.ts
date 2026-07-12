import type { EngineInputAsset } from '$lib/api/types';
import type { EngineRequest } from '../types';
import { compileAudioPromptReferences } from './prompt-references';
import {
	SEED_AUDIO_ENGINE_ID,
	activeSeedAudioDraft,
	createDefaultSeedAudioParameters,
	createDefaultSeedAudioState,
	seedAudioReferenceFromInputAsset,
	type SeedAudioFormat,
	type SeedAudioImageAsset,
	type SeedAudioMode,
	type SeedAudioParameters,
	type SeedAudioSampleRate,
	type SeedAudioState
} from './state';
import { validateSeedAudioEnvelope, validateSeedAudioState } from './validation';

export interface SeedAudioRequestEnvelope extends EngineRequest {
	engine_id: typeof SEED_AUDIO_ENGINE_ID;
	text: string;
	input_mode: SeedAudioMode;
	input_assets: EngineInputAsset[];
	engine_parameters: SeedAudioEngineParameters;
}

export interface SeedAudioEngineParameters extends Record<string, unknown> {
	format: SeedAudioFormat;
	sample_rate: SeedAudioSampleRate;
	enable_subtitle: boolean;
	speech_rate: number;
	loudness_rate: number;
	pitch_rate: number;
	aigc_watermark: boolean;
	aigc_metadata_enable: boolean;
	content_producer: string;
	produce_id: string;
	content_propagator: string;
	propagate_id: string;
	confirm_upload?: boolean;
}

export function seedAudioParametersToRequest(parameters: SeedAudioParameters): SeedAudioEngineParameters {
	return {
		format: parameters.format,
		sample_rate: parameters.sample_rate,
		enable_subtitle: parameters.enable_subtitle,
		speech_rate: parameters.speech_rate,
		loudness_rate: parameters.loudness_rate,
		pitch_rate: parameters.pitch_rate,
		aigc_watermark: parameters.aigc_watermark,
		aigc_metadata_enable: parameters.aigc_metadata.enable,
		...parameters.aigc_metadata.metadata
	};
}

function referenceInputAsset(
	asset: NonNullable<SeedAudioState['drafts']['audio']['references'][number]['asset']>
): EngineInputAsset {
	if (asset.type === 'speaker') {
		return {
			asset_id: asset.assetId,
			type: 'speaker',
			source: 'cloud_speaker',
			speaker_id: asset.speakerId,
			display_name: asset.displayName,
			license_status: asset.licenseStatus || null
		};
	}
	const audio = asset.referenceAudio!;
	return {
		asset_id: asset.assetId,
		type: 'audio',
		source: asset.source,
		file_id: audio.clip.fileId,
		voice_id: asset.voiceId || null,
		display_name: asset.displayName,
		ref_text: audio.transcript.text || null,
		source_file_id: audio.source.fileId || null,
		clip_file_id: audio.clip.fileId || null,
		trim_start_ms: audio.trim.startMs,
		trim_end_ms: audio.trim.endMs,
		duration_ms: audio.clip.durationMs,
		mime_type: audio.clip.mimeType || null,
		size_bytes: audio.clip.sizeBytes,
		license_status: asset.licenseStatus || null
	};
}

function imageInputAsset(image: SeedAudioImageAsset): EngineInputAsset {
	return {
		asset_id: image.assetId,
		type: 'image',
		source: image.source,
		file_id: image.fileId,
		display_name: image.displayName,
		mime_type: image.mimeType,
		size_bytes: image.sizeBytes,
		license_status: image.licenseStatus || null
	};
}

export function seedAudioStateToRequest(state: SeedAudioState): SeedAudioRequestEnvelope {
	const validation = validateSeedAudioState(state);
	if (validation.errors.length) {
		throw new Error(validation.errors.map((entry) => entry.message).join('；'));
	}
	const draft = activeSeedAudioDraft(state);
	let text = draft.prompt.trim();
	let inputAssets: EngineInputAsset[] = [];
	if (draft.mode === 'audio') {
		const compiled = compileAudioPromptReferences(text, draft.references);
		text = compiled.prompt;
		inputAssets = draft.references.filter((slot) => slot.asset).map((slot) => referenceInputAsset(slot.asset!));
	}
	if (draft.mode === 'image' && draft.image) inputAssets = [imageInputAsset(draft.image)];
	return {
		engine_id: SEED_AUDIO_ENGINE_ID,
		text,
		input_mode: draft.mode,
		input_assets: inputAssets,
		engine_parameters: seedAudioParametersToRequest(draft.parameters)
	};
}

function isMode(value: unknown): value is SeedAudioMode {
	return value === 'text' || value === 'audio' || value === 'image';
}

function parametersFromRequest(value: unknown): SeedAudioParameters {
	const defaults = createDefaultSeedAudioParameters();
	if (!value || typeof value !== 'object' || Array.isArray(value)) return defaults;
	const record = value as Record<string, unknown>;
	return {
		format: (record.format ?? defaults.format) as SeedAudioFormat,
		sample_rate: Number(record.sample_rate ?? defaults.sample_rate) as SeedAudioSampleRate,
		enable_subtitle: Boolean(record.enable_subtitle ?? defaults.enable_subtitle),
		speech_rate: Number(record.speech_rate ?? defaults.speech_rate),
		loudness_rate: Number(record.loudness_rate ?? defaults.loudness_rate),
		pitch_rate: Number(record.pitch_rate ?? defaults.pitch_rate),
		aigc_watermark: Boolean(record.aigc_watermark ?? defaults.aigc_watermark),
		aigc_metadata: {
			enable: Boolean(record.aigc_metadata_enable ?? defaults.aigc_metadata.enable),
			metadata: {
				content_producer: String(record.content_producer ?? ''),
				produce_id: String(record.produce_id ?? ''),
				content_propagator: String(record.content_propagator ?? ''),
				propagate_id: String(record.propagate_id ?? '')
			}
		}
	};
}

export function seedAudioStateFromRequest(request: Readonly<EngineRequest>): SeedAudioState {
	if (request.engine_id !== SEED_AUDIO_ENGINE_ID) throw new Error(`不能用 Seed Audio Profile 恢复引擎 ${String(request.engine_id)}`);
	const mode = request.input_mode;
	if (!isMode(mode)) throw new Error(`不支持的 Seed Audio 模式：${String(mode)}`);
	const assets = Array.isArray(request.input_assets) ? (request.input_assets as EngineInputAsset[]) : [];
	const envelopeErrors = validateSeedAudioEnvelope(mode, assets);
	if (envelopeErrors.length) throw new Error(envelopeErrors.map((entry) => entry.message).join('；'));
	const state = createDefaultSeedAudioState();
	state.mode = mode;
	const parameters = parametersFromRequest(request.engine_parameters);
	const prompt = String(request.text ?? '');
	if (mode === 'text') state.drafts.text = { ...state.drafts.text, prompt, parameters };
	if (mode === 'audio') {
		state.drafts.audio = {
			...state.drafts.audio,
			prompt,
			parameters,
			references: state.drafts.audio.references.map((slot, index) => ({
				...slot,
				asset: assets[index] ? seedAudioReferenceFromInputAsset(assets[index], index) : null
			})) as SeedAudioState['drafts']['audio']['references']
		};
	}
	if (mode === 'image') {
		const asset = assets[0];
		state.drafts.image = {
			...state.drafts.image,
			prompt,
			parameters,
			image: asset
				? {
						assetId: asset.asset_id,
						source: asset.source === 'preset' ? 'preset' : 'upload',
						fileId: asset.file_id ?? '',
						displayName: asset.display_name ?? '参考图片',
						previewUrl: '',
						mimeType: asset.mime_type ?? '',
						sizeBytes: asset.size_bytes ?? null,
						licenseStatus: asset.license_status ?? ''
					}
				: null
		};
	}
	return state;
}
