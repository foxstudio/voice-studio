import type { EngineInputAsset, EngineInputAssetSource } from '$lib/api/types';
import { createReferenceAudioDraft, type ReferenceAudioDraft } from '../reference-audio/draft';

export const SEED_AUDIO_ENGINE_ID = 'doubao-seed-audio-1.0';
export const SEED_AUDIO_MAX_PROMPT_CHARS = 3000;
export const SEED_AUDIO_MAX_REFERENCES = 3;
export const SEED_AUDIO_MAX_ASSET_BYTES = 10 * 1024 * 1024;
export const SEED_AUDIO_MAX_REFERENCE_DURATION_MS = 30_000;

export type SeedAudioMode = 'text' | 'audio' | 'image';
export type SeedAudioFormat = 'wav' | 'mp3' | 'pcm' | 'ogg_opus';
export type SeedAudioSampleRate = 8000 | 16000 | 24000 | 32000 | 44100 | 48000;

export interface SeedAudioWatermarkMetadata {
	content_producer: string;
	produce_id: string;
	content_propagator: string;
	propagate_id: string;
}

export interface SeedAudioParameters {
	format: SeedAudioFormat;
	sample_rate: SeedAudioSampleRate;
	enable_subtitle: boolean;
	speech_rate: number;
	loudness_rate: number;
	pitch_rate: number;
	aigc_watermark: boolean;
	aigc_metadata: {
		enable: boolean;
		metadata: SeedAudioWatermarkMetadata;
	};
}

export interface SeedAudioReferenceAsset {
	assetId: string;
	type: 'audio' | 'speaker';
	source: EngineInputAssetSource;
	displayName: string;
	voiceId: string;
	speakerId: string;
	licenseStatus: string;
	referenceAudio: ReferenceAudioDraft | null;
}

export interface SeedAudioReferenceSlot {
	slot: 1 | 2 | 3;
	asset: SeedAudioReferenceAsset | null;
}

export interface SeedAudioImageAsset {
	assetId: string;
	source: Extract<EngineInputAssetSource, 'upload' | 'preset'>;
	fileId: string;
	displayName: string;
	previewUrl: string;
	mimeType: string;
	sizeBytes: number | null;
	licenseStatus: string;
}

interface SeedAudioModeDraftBase {
	prompt: string;
	parameters: SeedAudioParameters;
}

export interface SeedAudioTextDraft extends SeedAudioModeDraftBase {
	mode: 'text';
}

export interface SeedAudioReferenceDraft extends SeedAudioModeDraftBase {
	mode: 'audio';
	references: [SeedAudioReferenceSlot, SeedAudioReferenceSlot, SeedAudioReferenceSlot];
}

export interface SeedAudioImageDraft extends SeedAudioModeDraftBase {
	mode: 'image';
	image: SeedAudioImageAsset | null;
}

export interface SeedAudioState {
	mode: SeedAudioMode;
	drafts: {
		text: SeedAudioTextDraft;
		audio: SeedAudioReferenceDraft;
		image: SeedAudioImageDraft;
	};
}

export type SeedAudioActiveDraft = SeedAudioTextDraft | SeedAudioReferenceDraft | SeedAudioImageDraft;

export function createDefaultSeedAudioParameters(): SeedAudioParameters {
	return {
		format: 'wav',
		sample_rate: 24000,
		enable_subtitle: false,
		speech_rate: 0,
		loudness_rate: 0,
		pitch_rate: 0,
		aigc_watermark: false,
		aigc_metadata: {
			enable: false,
			metadata: { content_producer: '', produce_id: '', content_propagator: '', propagate_id: '' }
		}
	};
}

function createReferenceSlots(): SeedAudioReferenceDraft['references'] {
	return [1, 2, 3].map((slot) => ({ slot: slot as 1 | 2 | 3, asset: null })) as SeedAudioReferenceDraft['references'];
}

export function createDefaultSeedAudioState(): SeedAudioState {
	return {
		mode: 'text',
		drafts: {
			text: { mode: 'text', prompt: '', parameters: createDefaultSeedAudioParameters() },
			audio: {
				mode: 'audio',
				prompt: '',
				parameters: createDefaultSeedAudioParameters(),
				references: createReferenceSlots()
			},
			image: { mode: 'image', prompt: '', parameters: createDefaultSeedAudioParameters(), image: null }
		}
	};
}

export function activeSeedAudioDraft(state: SeedAudioState): SeedAudioActiveDraft {
	return state.drafts[state.mode];
}

export function setSeedAudioMode(state: SeedAudioState, mode: SeedAudioMode): SeedAudioState {
	return { ...state, mode };
}

export function updateSeedAudioPrompt(state: SeedAudioState, prompt: string): SeedAudioState {
	return {
		...state,
		drafts: { ...state.drafts, [state.mode]: { ...activeSeedAudioDraft(state), prompt } }
	};
}

export function updateSeedAudioParameters(
	state: SeedAudioState,
	patch: Partial<SeedAudioParameters>
): SeedAudioState {
	const active = activeSeedAudioDraft(state);
	const parameters: SeedAudioParameters = {
		...active.parameters,
		...patch,
		aigc_metadata: patch.aigc_metadata
			? {
					...active.parameters.aigc_metadata,
					...patch.aigc_metadata,
					metadata: {
						...active.parameters.aigc_metadata.metadata,
						...patch.aigc_metadata.metadata
					}
				}
			: active.parameters.aigc_metadata
	};
	return { ...state, drafts: { ...state.drafts, [state.mode]: { ...active, parameters } } };
}

export function setSeedAudioReference(
	state: SeedAudioState,
	slot: 1 | 2 | 3,
	asset: SeedAudioReferenceAsset | null
): SeedAudioState {
	const audio = state.drafts.audio;
	const references = audio.references.map((entry) =>
		entry.slot === slot ? { ...entry, asset } : entry
	) as SeedAudioReferenceDraft['references'];
	return { ...state, drafts: { ...state.drafts, audio: { ...audio, references } } };
}

export function setSeedAudioImage(state: SeedAudioState, image: SeedAudioImageAsset | null): SeedAudioState {
	return { ...state, drafts: { ...state.drafts, image: { ...state.drafts.image, image } } };
}

export function resetActiveSeedAudioParameters(state: SeedAudioState): SeedAudioState {
	const active = activeSeedAudioDraft(state);
	return {
		...state,
		drafts: {
			...state.drafts,
			[state.mode]: { ...active, parameters: createDefaultSeedAudioParameters() }
		}
	};
}

export function seedAudioReferenceFromInputAsset(asset: EngineInputAsset, index: number): SeedAudioReferenceAsset {
	if (asset.type === 'speaker') {
		return {
			assetId: asset.asset_id,
			type: 'speaker',
			source: 'cloud_speaker',
			displayName: asset.display_name ?? asset.speaker_id ?? `云端音色 ${index + 1}`,
			voiceId: asset.voice_id ?? '',
			speakerId: asset.speaker_id ?? '',
			licenseStatus: asset.license_status ?? '',
			referenceAudio: null
		};
	}
	const referenceAudio = createReferenceAudioDraft(asset.asset_id, {
		sourceKind: asset.source === 'voice_library' ? 'voice_library' : asset.source === 'preset' ? 'preset' : 'history',
		source: {
			fileId: asset.source_file_id ?? asset.file_id ?? '',
			fileName: asset.display_name ?? `参考声音 ${index + 1}`,
			durationMs: asset.duration_ms ?? null
		},
		clip: {
			fileId: asset.clip_file_id ?? asset.file_id ?? '',
			fileName: asset.display_name ?? `参考声音 ${index + 1}`,
			durationMs: asset.duration_ms ?? null
		},
		trim: { startMs: asset.trim_start_ms ?? null, endMs: asset.trim_end_ms ?? null },
		transcript: { text: asset.ref_text ?? '' }
	});
	return {
		assetId: asset.asset_id,
		type: 'audio',
		source: asset.source,
		displayName: asset.display_name ?? `参考声音 ${index + 1}`,
		voiceId: asset.voice_id ?? '',
		speakerId: '',
		licenseStatus: asset.license_status ?? '',
		referenceAudio
	};
}
