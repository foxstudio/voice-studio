import type { EngineInputAsset } from '$lib/api/types';
import type { EngineValidationIssue, EngineValidationResult } from '../types';
import { validateAudioPromptReferences } from './prompt-references';
import {
	SEED_AUDIO_MAX_ASSET_BYTES,
	SEED_AUDIO_MAX_PROMPT_CHARS,
	SEED_AUDIO_MAX_REFERENCE_DURATION_MS,
	activeSeedAudioDraft,
	type SeedAudioParameters,
	type SeedAudioState
} from './state';

const OUTPUT_FORMATS = new Set(['wav', 'mp3', 'pcm', 'ogg_opus']);
const SAMPLE_RATES = new Set([8000, 16000, 24000, 32000, 44100, 48000]);
const AUDIO_MIME_TYPES = new Set(['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp3', 'audio/pcm', 'audio/ogg', 'audio/opus']);
const IMAGE_MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const CLOUD_ALLOWED_LICENSES = new Set(['self_voice', 'authorized', 'company_authorized']);

function issue(code: string, message: string, path?: string): EngineValidationIssue {
	return { code, message, path };
}

function rangeError(
	errors: EngineValidationIssue[],
	value: number,
	min: number,
	max: number,
	path: string,
	label: string
) {
	if (!Number.isFinite(value) || value < min || value > max) {
		errors.push(issue('parameter_out_of_range', `${label}必须在 ${min} 到 ${max} 之间`, path));
	}
}

export function validateSeedAudioParameters(parameters: SeedAudioParameters): EngineValidationIssue[] {
	const errors: EngineValidationIssue[] = [];
	if (!OUTPUT_FORMATS.has(parameters.format)) errors.push(issue('format_unsupported', '输出格式不受支持', 'parameters.format'));
	if (!SAMPLE_RATES.has(parameters.sample_rate)) errors.push(issue('sample_rate_unsupported', '采样率不受支持', 'parameters.sample_rate'));
	rangeError(errors, parameters.speech_rate, -50, 100, 'parameters.speech_rate', '语速');
	rangeError(errors, parameters.loudness_rate, -50, 100, 'parameters.loudness_rate', '音量');
	rangeError(errors, parameters.pitch_rate, -12, 12, 'parameters.pitch_rate', '音调');
	return errors;
}

function validateInputAsset(asset: EngineInputAsset, path: string): EngineValidationIssue[] {
	const errors: EngineValidationIssue[] = [];
	if (!CLOUD_ALLOWED_LICENSES.has(asset.license_status ?? '')) {
		errors.push(issue('asset_license_denied', '该素材没有获得云端上传授权', `${path}.license_status`));
	}
	if (asset.size_bytes !== null && asset.size_bytes !== undefined && asset.size_bytes > SEED_AUDIO_MAX_ASSET_BYTES) {
		errors.push(issue('asset_too_large', '素材不能超过 10MB', `${path}.size_bytes`));
	}
	if (asset.type === 'audio') {
		if (asset.duration_ms !== null && asset.duration_ms !== undefined && asset.duration_ms > SEED_AUDIO_MAX_REFERENCE_DURATION_MS) {
			errors.push(issue('audio_too_long', '每条参考声音不能超过 30 秒', `${path}.duration_ms`));
		}
		if (asset.mime_type && !AUDIO_MIME_TYPES.has(asset.mime_type)) {
			errors.push(issue('audio_format_unsupported', '参考声音仅支持 WAV、MP3、PCM 或 OGG Opus', `${path}.mime_type`));
		}
	}
	if (asset.type === 'image' && asset.mime_type && !IMAGE_MIME_TYPES.has(asset.mime_type)) {
		errors.push(issue('image_format_unsupported', '参考图片仅支持 JPEG、PNG 或 WebP', `${path}.mime_type`));
	}
	return errors;
}

export function validateSeedAudioState(state: SeedAudioState): EngineValidationResult {
	const draft = activeSeedAudioDraft(state);
	const errors: EngineValidationIssue[] = [];
	const warnings: EngineValidationIssue[] = [];
	if (!draft.prompt.trim()) errors.push(issue('prompt_required', '请输入生成描述', 'prompt'));
	if (draft.prompt.length > SEED_AUDIO_MAX_PROMPT_CHARS) {
		errors.push(issue('prompt_too_long', `生成描述不能超过 ${SEED_AUDIO_MAX_PROMPT_CHARS} 字符`, 'prompt'));
	}
	errors.push(...validateSeedAudioParameters(draft.parameters));

	if (draft.mode === 'audio') {
		const filled = draft.references.filter((slot) => slot.asset);
		if (!filled.length) errors.push(issue('audio_reference_required', '请至少添加一条参考声音', 'references'));
		for (const slot of filled) {
			const asset = slot.asset!;
			if (asset.type === 'speaker') {
				if (!asset.speakerId.trim()) errors.push(issue('speaker_id_required', '云端音色缺少 speaker ID', `references.${slot.slot}`));
				continue;
			}
			const audio = asset.referenceAudio;
			if (!audio?.clip.fileId) {
				errors.push(issue('audio_file_required', '参考声音缺少可提交的音频文件', `references.${slot.slot}`));
				continue;
			}
			if (asset.source === 'voice_library' && !asset.voiceId.trim()) {
				errors.push(issue('voice_id_required', '音色库参考声音缺少 voice ID', `references.${slot.slot}`));
			}
			errors.push(
				...validateInputAsset(
					{
						asset_id: asset.assetId,
						type: 'audio',
						source: asset.source,
						duration_ms: audio.clip.durationMs,
						mime_type: audio.clip.mimeType,
						size_bytes: audio.clip.sizeBytes,
						license_status: asset.licenseStatus
					},
					`references.${slot.slot}`
				)
			);
		}
		const promptValidation = validateAudioPromptReferences(draft.prompt, draft.references);
		errors.push(...promptValidation.errors.map((entry) => issue(entry.code, entry.message, `prompt.@音频${entry.slot}`)));
		warnings.push(...promptValidation.warnings.map((entry) => issue(entry.code, entry.message, `prompt.@音频${entry.slot}`)));
	}

	if (draft.mode === 'image') {
		if (!draft.image) errors.push(issue('image_required', '请添加一张参考图片', 'image'));
		else {
			if (!draft.image.fileId.trim()) errors.push(issue('image_file_required', '参考图片缺少可提交的受管理文件', 'image'));
			errors.push(
				...validateInputAsset(
					{
						asset_id: draft.image.assetId,
						type: 'image',
						source: draft.image.source,
						file_id: draft.image.fileId,
						mime_type: draft.image.mimeType,
						size_bytes: draft.image.sizeBytes,
						license_status: draft.image.licenseStatus
					},
					'image'
				)
			);
		}
	}
	return { errors, warnings };
}

export function validateSeedAudioEnvelope(mode: string, assets: readonly EngineInputAsset[]): EngineValidationIssue[] {
	const errors: EngineValidationIssue[] = [];
	const audioAssets = assets.filter((asset) => asset.type === 'audio' || asset.type === 'speaker');
	const imageAssets = assets.filter((asset) => asset.type === 'image');
	if (audioAssets.length > 3) errors.push(issue('too_many_audio_references', '最多只能提交三条参考声音', 'input_assets'));
	if (imageAssets.length > 1) errors.push(issue('too_many_images', '最多只能提交一张参考图片', 'input_assets'));
	if (audioAssets.length && imageAssets.length) errors.push(issue('mixed_reference_types', '参考声音和参考图片不能同时提交', 'input_assets'));
	if (mode === 'text' && assets.length) errors.push(issue('text_mode_assets_forbidden', '文字描述模式不能提交参考素材', 'input_assets'));
	if (mode === 'audio' && audioAssets.length === 0) errors.push(issue('audio_reference_required', '参考声音模式至少需要一条声音', 'input_assets'));
	if (mode === 'audio' && imageAssets.length) errors.push(issue('audio_mode_image_forbidden', '参考声音模式不能提交图片', 'input_assets'));
	if (mode === 'image' && imageAssets.length !== 1) errors.push(issue('image_required', '参考图片模式必须包含一张图片', 'input_assets'));
	if (mode === 'image' && audioAssets.length) errors.push(issue('image_mode_audio_forbidden', '参考图片模式不能提交声音', 'input_assets'));
	return errors;
}
