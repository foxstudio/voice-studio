import { get, writable } from 'svelte/store';
import type {
	EngineDetail,
	EngineSpeaker,
	GenerationTask,
	GeneratePlanResponse,
	GenerateRequest,
	LongformTask,
	PresetTemplate,
	TTSVerificationResponse,
	VoiceAsset,
	AppSettings
} from '$lib/api/types';
import {
	INDEX_TTS_ENGINE_ID,
	createDefaultIndexTtsEmotionState,
	type IndexTtsEmotionState
} from '../../routes/generate/engine-ui/indextts-v2/state';
import {
	indexTtsEmotionStateFromRequest,
	indexTtsEmotionStateToRequest
} from '../../routes/generate/engine-ui/indextts-v2/request';

export type TaskStatusTab = 'all' | 'active' | 'success' | 'failed';
export type TaskSourceFilter = 'all' | 'local' | 'cloud';
export type TaskDateFilter = 'all' | 'today' | '7d' | '30d';
export type TaskSortBy = 'latest' | 'oldest' | 'duration_desc';
export type LongformStrategy = 'split_merge' | 'split_only' | 'single';
export type VoiceSourceMode = 'voice_library' | 'reference_audio';

export type PresetDraft = {
	name: string;
	scene: string;
	description: string;
	tags: string;
	sample_text: string;
};

export type GenerateStoreState = {
	engines: EngineDetail[];
	voices: VoiceAsset[];
	presets: PresetTemplate[];
	settings: AppSettings | null;
	voicePreviewAudio: HTMLAudioElement | null;
	voicePreviewPlaying: boolean;
	resultPreviewAudio: HTMLAudioElement | null;
	playingResultTaskId: string;
	resultAudioPlaying: boolean;
	showPresetEditor: boolean;
	editingPresetId: string;
	presetBusy: boolean;
	presetDraft: PresetDraft;
	text: string;
	textSegments: string[];
	textToolBusy: 'clean' | 'numbers' | 'split' | '';
	showSplitPreview: boolean;
	splitPreviewCollapsed: boolean;
	lastGeneratePlan: GeneratePlanResponse | null;
	requestSource: string;
	requestProjectId: string;
	requestSegmentId: string;
	requestLocalizedSubtitleId: string;
	requestCueId: string;
	requestBindToVideoLocalization: boolean;
	requestReferenceLicenseStatus: string | null;
	requestReferenceTags: string[];
	engineId: string;
	/** Model-specific settings that are not shared legacy TTS fields. */
	engineParameters: Record<string, unknown>;
	engineUiStateById: Record<string, unknown>;
	voiceSource: VoiceSourceMode;
	voiceId: string;
	customVoiceFileName: string;
	customVoiceFileId: string;
	customVoicePreviewUrl: string;
	customVoiceReferenceAudioPath: string;
	customVoiceSourceFileId: string;
	customVoiceSourceAudioPath: string;
	customVoiceSourceDurationMs: number | null;
	customVoiceTrimStartMs: number | null;
	customVoiceTrimEndMs: number | null;
	customVoiceTranscript: string;
	customVoiceSrt: string;
	customVoiceDurationMs: number | null;
	customVoiceSrtSegmentCount: number;
	customVoiceTranscriptionId: string;
	customVoiceConfirmed: boolean;
	customVoiceBusy: boolean;
	customVoiceError: string;
	customVoiceQualityWarnings: string[];
	language: string;
	emotion: string;
	voiceDesign: string;
	voiceDesignPrompt: string;
	optimizeTextPreview: boolean;
	styleInstruction: string;
	mimoVoice: string;
	speakerId: string;
	speakerQuery: string;
	speakerGenderFilter: 'all' | 'F' | 'M';
	speakerCatalog: EngineSpeaker[];
	speakerCatalogLoading: boolean;
	speakerCatalogKey: string;
	voicePrompt: string;
	emoAlpha: number;
	speed: number;
	pitchRate: number;
	doubaoSampleRate: 8000 | 16000 | 22050 | 24000 | 32000 | 44100 | 48000;
	doubaoBitRate: number;
	doubaoLoudnessRate: number;
	doubaoEnableSubtitle: boolean;
	doubaoSilenceDuration: number;
	doubaoAigcWatermark: boolean;
	nfeStep: number;
	cfgStrength: number;
	targetRms: number;
	crossFadeDuration: number;
	swaySamplingCoef: number;
	fixDuration: number;
	removeSilence: boolean;
	temperature: number;
	topP: number;
	topK: number;
	maxTextTokensPerSegment: number;
	intervalSilence: number;
	diffusionSteps: number;
	cfgRate: number;
	guidanceScale: number;
	duration: number;
	audioChunkDuration: number;
	audioChunkThreshold: number;
	maxTokens: number;
	cfgScale: number | null;
	ddpmSteps: number | null;
	maxMelTokens: number;
	repetitionPenalty: number;
	seed: number | null;
	outputFormat: 'wav' | 'mp3' | 'flac' | 'pcm' | 'ogg_opus';
	showAdvanced: boolean;
	showMoreParams: boolean;
	tasks: GenerationTask[];
	longformTasks: LongformTask[];
	selectedTaskIds: string[];
	taskQuery: string;
	taskStatusTab: TaskStatusTab;
	taskEngineFilter: string;
	taskSourceFilter: TaskSourceFilter;
	taskDateFilter: TaskDateFilter;
	taskSortBy: TaskSortBy;
	currentPage: number;
	pageSize: number;
	pageSizeAuto: boolean;
	resultGridEl: HTMLDivElement | undefined;
	pageJumpInput: string;
	actionBusyTaskId: string;
	verificationBusyTaskId: string;
	verificationReports: Record<string, TTSVerificationResponse>;
	verificationErrors: Record<string, string>;
	showLongformDialog: boolean;
	pendingLongformPlan: GeneratePlanResponse | null;
	pendingLongformResolve: ((value: LongformStrategy | null) => void) | null;
	longformStrategy: LongformStrategy;
	longformVerifyEnabled: boolean;
	longformMergeEnabled: boolean;
	longformMaxRetries: number;
	busy: boolean;
	error: string;
	initialized: boolean;
	lastEngineId: string;
};

const INDEX_TTS_DEFAULTS = {
	emotion: '',
	emoAlpha: 0.6,
	speed: 1.0,
	temperature: 0.8,
	topP: 0.8,
	topK: 30,
	maxTextTokensPerSegment: 120,
	intervalSilence: 200,
	diffusionSteps: 25,
	cfgRate: 0.7,
	maxMelTokens: 1500,
	repetitionPenalty: 10,
	outputFormat: 'wav' as const
};

const MIMO_DEFAULTS = {
	temperature: 0.6,
	topP: 0.95
};

const OMNIVOICE_DEFAULTS = {
	diffusionSteps: 32,
	guidanceScale: 2.0,
	duration: 0,
	audioChunkDuration: 15,
	audioChunkThreshold: 30
};

const QWEN3_DEFAULTS = {
	maxTokens: 1200,
	cfgScale: 1.5 as number | null,
	ddpmSteps: null as number | null
};

const F5_DEFAULTS = {
	nfeStep: 32,
	cfgStrength: 2.0,
	targetRms: 0.1,
	crossFadeDuration: 0.15,
	swaySamplingCoef: -1.0,
	fixDuration: 0,
	removeSilence: false
};

const CONFUCIUS4_DEFAULTS = {
	temperature: 0.8,
	topP: 0.8,
	topK: 30,
	repetitionPenalty: 10,
	seed: 0
};

export const DOUBAO_TTS_DEFAULTS = {
	sampleRate: 48000 as const,
	bitRate: 160000
};

const DEFAULT_PRESET_DRAFT: PresetDraft = {
	name: '',
	scene: '',
	description: '',
	tags: '',
	sample_text: ''
};

const DEFAULT_VOICE_DESIGN = '女，青年，中音调';
const DEFAULT_VOICE_DESIGN_PROMPT = '中年男性，声线沉稳偏正式，吐字工整，语速适中。';
export const REFERENCE_VOICE_ENGINE_IDS = [
	'indextts-v2',
	'omnivoice',
	'confucius4-mlx-int8',
	'qwen3-tts-mlx-0.6b',
	'mimo-v2.5-tts-voiceclone',
	'doubao-tts-voiceclone',
	'f5-tts',
	'cosyvoice-zero-shot'
] as const;

// 豆包声音复刻必须先训练为云端音色，再从音色库选择；它不接受一段
// 本地参考音频直接合成，因此不能进入“自定义上传”的请求分支。
const DIRECT_CUSTOM_REFERENCE_ENGINE_IDS = [
	'indextts-v2',
	'omnivoice',
	'confucius4-mlx-int8',
	'qwen3-tts-mlx-0.6b',
	'mimo-v2.5-tts-voiceclone',
	'f5-tts',
	'cosyvoice-zero-shot'
] as const;

// These keys already have dedicated state and request fields.  All other
// manifest parameters stay in `engineParameters`, which lets a model grow
// model-specific advanced controls without adding another global store field.
const LEGACY_REQUEST_PARAMETER_KEYS = new Set([
	'language', 'speaker_id', 'prompt', 'style_instruction', 'voice_design_prompt', 'mimo_voice',
	'optimize_text_preview', 'emotion', 'emo_alpha', 'speed', 'pitch_rate', 'sample_rate', 'bit_rate',
	'loudness_rate', 'enable_subtitle', 'silence_duration', 'aigc_watermark', 'temperature', 'top_p',
	'top_k', 'repetition_penalty', 'seed', 'max_mel_tokens', 'max_text_tokens_per_segment',
	'interval_silence', 'diffusion_steps', 'cfg_rate', 'guidance_scale', 'duration',
	'audio_chunk_duration', 'audio_chunk_threshold', 'max_tokens', 'cfg_scale', 'ddpm_steps',
	'nfe_step', 'cfg_strength', 'target_rms', 'cross_fade_duration', 'sway_sampling_coef',
	'fix_duration', 'remove_silence', 'emotion_text'
]);

/**
 * Keep generic model-specific controls separate from the long-lived request
 * fields above. Presets use this too, so an advanced control cannot render
 * correctly and then silently disappear when that preset is applied.
 */
export function pickEngineSpecificParameters(parameters: Record<string, unknown>) {
	return Object.fromEntries(
		Object.entries(parameters).filter(([key]) => !LEGACY_REQUEST_PARAMETER_KEYS.has(key))
	);
}

function createInitialState(): GenerateStoreState {
	return {
		engines: [],
		voices: [],
		presets: [],
		settings: null,
		voicePreviewAudio: null,
		voicePreviewPlaying: false,
		resultPreviewAudio: null,
		playingResultTaskId: '',
		resultAudioPlaying: false,
		showPresetEditor: false,
		editingPresetId: '',
		presetBusy: false,
		presetDraft: { ...DEFAULT_PRESET_DRAFT },
		text: '',
		textSegments: [],
		textToolBusy: '',
		showSplitPreview: false,
		splitPreviewCollapsed: false,
		lastGeneratePlan: null,
		requestSource: '',
		requestProjectId: '',
		requestSegmentId: '',
		requestLocalizedSubtitleId: '',
		requestCueId: '',
		requestBindToVideoLocalization: false,
		requestReferenceLicenseStatus: null,
		requestReferenceTags: [],
		engineId: 'indextts-v2',
		engineParameters: {},
		engineUiStateById: { [INDEX_TTS_ENGINE_ID]: createDefaultIndexTtsEmotionState() },
		voiceSource: 'voice_library',
		voiceId: '',
		customVoiceFileName: '',
		customVoiceFileId: '',
		customVoicePreviewUrl: '',
		customVoiceReferenceAudioPath: '',
		customVoiceSourceFileId: '',
		customVoiceSourceAudioPath: '',
		customVoiceSourceDurationMs: null,
		customVoiceTrimStartMs: null,
		customVoiceTrimEndMs: null,
		customVoiceTranscript: '',
		customVoiceSrt: '',
		customVoiceDurationMs: null,
		customVoiceSrtSegmentCount: 0,
		customVoiceTranscriptionId: '',
		customVoiceConfirmed: false,
		customVoiceBusy: false,
		customVoiceError: '',
		customVoiceQualityWarnings: [],
		language: 'zh',
		emotion: '',
		voiceDesign: DEFAULT_VOICE_DESIGN,
		voiceDesignPrompt: DEFAULT_VOICE_DESIGN_PROMPT,
		optimizeTextPreview: false,
		styleInstruction: '',
		mimoVoice: 'mimo_default',
		speakerId: '',
		speakerQuery: '',
		speakerGenderFilter: 'all',
		speakerCatalog: [],
		speakerCatalogLoading: false,
		speakerCatalogKey: '',
		voicePrompt: '',
		emoAlpha: INDEX_TTS_DEFAULTS.emoAlpha,
		speed: INDEX_TTS_DEFAULTS.speed,
		pitchRate: 0,
		doubaoSampleRate: DOUBAO_TTS_DEFAULTS.sampleRate,
		doubaoBitRate: DOUBAO_TTS_DEFAULTS.bitRate,
		doubaoLoudnessRate: 0,
		doubaoEnableSubtitle: false,
		doubaoSilenceDuration: 0,
		doubaoAigcWatermark: false,
		nfeStep: F5_DEFAULTS.nfeStep,
		cfgStrength: F5_DEFAULTS.cfgStrength,
		targetRms: F5_DEFAULTS.targetRms,
		crossFadeDuration: F5_DEFAULTS.crossFadeDuration,
		swaySamplingCoef: F5_DEFAULTS.swaySamplingCoef,
		fixDuration: F5_DEFAULTS.fixDuration,
		removeSilence: F5_DEFAULTS.removeSilence,
		temperature: INDEX_TTS_DEFAULTS.temperature,
		topP: INDEX_TTS_DEFAULTS.topP,
		topK: INDEX_TTS_DEFAULTS.topK,
		maxTextTokensPerSegment: INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: INDEX_TTS_DEFAULTS.diffusionSteps,
		cfgRate: INDEX_TTS_DEFAULTS.cfgRate,
		guidanceScale: OMNIVOICE_DEFAULTS.guidanceScale,
		duration: OMNIVOICE_DEFAULTS.duration,
		audioChunkDuration: OMNIVOICE_DEFAULTS.audioChunkDuration,
		audioChunkThreshold: OMNIVOICE_DEFAULTS.audioChunkThreshold,
		maxTokens: QWEN3_DEFAULTS.maxTokens,
		cfgScale: QWEN3_DEFAULTS.cfgScale,
		ddpmSteps: QWEN3_DEFAULTS.ddpmSteps,
		maxMelTokens: INDEX_TTS_DEFAULTS.maxMelTokens,
		repetitionPenalty: INDEX_TTS_DEFAULTS.repetitionPenalty,
		seed: null,
		outputFormat: INDEX_TTS_DEFAULTS.outputFormat,
		showAdvanced: false,
		showMoreParams: false,
		tasks: [],
		longformTasks: [],
		selectedTaskIds: [],
		taskQuery: '',
		taskStatusTab: 'all',
		taskEngineFilter: 'all',
		taskSourceFilter: 'all',
		taskDateFilter: 'all',
		taskSortBy: 'latest',
		currentPage: 1,
		pageSize: 10,
		pageSizeAuto: false,
		resultGridEl: undefined,
		pageJumpInput: '',
		actionBusyTaskId: '',
		verificationBusyTaskId: '',
		verificationReports: {},
		verificationErrors: {},
		showLongformDialog: false,
		pendingLongformPlan: null,
		pendingLongformResolve: null,
		longformStrategy: 'split_merge',
		longformVerifyEnabled: true,
		longformMergeEnabled: true,
		longformMaxRetries: 2,
		busy: false,
		error: '',
		initialized: false,
		lastEngineId: 'indextts-v2'
	};
}

function isMimoEngine(engineId: string) {
	return engineId.startsWith('mimo-v2.5');
}

function getSelectedEngine(state: GenerateStoreState, engineId = state.engineId) {
	return state.engines.find((engine) => engine.manifest.engine_id === engineId);
}

function getActiveParamKeys(state: GenerateStoreState, engineId = state.engineId) {
	return new Set(getSelectedEngine(state, engineId)?.manifest.parameter_schema.map((param) => param.key) ?? []);
}

function getEngineDefaults(state: GenerateStoreState, engineId: string) {
	const selectedEngine = getSelectedEngine(state, engineId);
	const parameterSchema = selectedEngine?.manifest.parameter_schema ?? [];
	const speakerParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'speaker_id');
	const promptParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'prompt');
	const styleParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'style_instruction');
	const voiceDesignParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'voice_design_prompt');
	const languageParam = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'language');
	const parameterDefault = (key: string, fallback: unknown) =>
		selectedEngine?.manifest.parameter_schema.find((param) => param.key === key)?.default ?? fallback;
	const nullableNumberDefault = (key: string, fallback: number | null) => {
		const value = parameterDefault(key, fallback);
		return value === null || value === undefined || value === '' ? null : Number(value);
	};
	const seedDefault = selectedEngine?.manifest.parameter_schema.find((param) => param.key === 'seed')?.default;
	const engineParameters = pickEngineSpecificParameters(
		Object.fromEntries(parameterSchema.map((parameter) => [parameter.key, parameter.default]))
	);

	return {
		engineId,
		lastEngineId: engineId,
		engineParameters,
		showAdvanced: engineId === 'f5-tts',
		language: String(languageParam?.default ?? state.language),
		emotion: INDEX_TTS_DEFAULTS.emotion,
		emoAlpha: INDEX_TTS_DEFAULTS.emoAlpha,
		speed: INDEX_TTS_DEFAULTS.speed,
		pitchRate: Number(parameterDefault('pitch_rate', 0)),
		doubaoSampleRate: Number(parameterDefault('sample_rate', DOUBAO_TTS_DEFAULTS.sampleRate)) as GenerateStoreState['doubaoSampleRate'],
		doubaoBitRate: Number(parameterDefault('bit_rate', DOUBAO_TTS_DEFAULTS.bitRate)),
		doubaoLoudnessRate: Number(parameterDefault('loudness_rate', 0)),
		doubaoEnableSubtitle: Boolean(parameterDefault('enable_subtitle', false)),
		doubaoSilenceDuration: Number(parameterDefault('silence_duration', 0)),
		doubaoAigcWatermark: Boolean(parameterDefault('aigc_watermark', false)),
		temperature: Number(parameterDefault('temperature', isMimoEngine(engineId) ? MIMO_DEFAULTS.temperature : engineId === 'confucius4-mlx-int8' ? CONFUCIUS4_DEFAULTS.temperature : INDEX_TTS_DEFAULTS.temperature)),
		topP: Number(parameterDefault('top_p', isMimoEngine(engineId) ? MIMO_DEFAULTS.topP : CONFUCIUS4_DEFAULTS.topP)),
		topK: Number(parameterDefault('top_k', CONFUCIUS4_DEFAULTS.topK)),
		maxTextTokensPerSegment: INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: engineId === 'omnivoice' ? OMNIVOICE_DEFAULTS.diffusionSteps : Number(parameterDefault('diffusion_steps', INDEX_TTS_DEFAULTS.diffusionSteps)),
		cfgRate: Number(parameterDefault('cfg_rate', INDEX_TTS_DEFAULTS.cfgRate)),
		guidanceScale: OMNIVOICE_DEFAULTS.guidanceScale,
		duration: OMNIVOICE_DEFAULTS.duration,
		outputFormat: INDEX_TTS_DEFAULTS.outputFormat,
		maxTokens: Number(parameterDefault('max_tokens', QWEN3_DEFAULTS.maxTokens)),
		cfgScale: nullableNumberDefault('cfg_scale', QWEN3_DEFAULTS.cfgScale),
		ddpmSteps: nullableNumberDefault('ddpm_steps', QWEN3_DEFAULTS.ddpmSteps),
		nfeStep: F5_DEFAULTS.nfeStep,
		cfgStrength: F5_DEFAULTS.cfgStrength,
		targetRms: F5_DEFAULTS.targetRms,
		crossFadeDuration: F5_DEFAULTS.crossFadeDuration,
		swaySamplingCoef: Number(parameterDefault('sway_sampling_coef', F5_DEFAULTS.swaySamplingCoef)),
		fixDuration: Number(parameterDefault('fix_duration', F5_DEFAULTS.fixDuration)),
		removeSilence: F5_DEFAULTS.removeSilence,
		repetitionPenalty: Number(parameterDefault('repetition_penalty', CONFUCIUS4_DEFAULTS.repetitionPenalty)),
		seed: seedDefault === undefined || seedDefault === null ? null : Number(seedDefault),
		speakerId: String(speakerParam?.default ?? speakerParam?.options?.[0]?.value ?? ''),
		styleInstruction: String(styleParam?.default ?? ''),
		voiceDesignPrompt: String(voiceDesignParam?.default || (engineId === 'mimo-v2.5-tts-voicedesign' ? DEFAULT_VOICE_DESIGN_PROMPT : '')),
		voicePrompt: String(promptParam?.default ?? promptParam?.options?.[0]?.value ?? '')
	};
}

function createRequest(state: GenerateStoreState): GenerateRequest {
	const selected = getSelectedEngine(state);
	const activeParamKeys = getActiveParamKeys(state);
	const selectedVoice = state.voices.find((voice) => voice.voice_id === state.voiceId) ?? null;
	const usesReferenceVoice = Boolean(selected && REFERENCE_VOICE_ENGINE_IDS.includes(selected.manifest.engine_id as (typeof REFERENCE_VOICE_ENGINE_IDS)[number]));
	const acceptsDirectCustomReference = Boolean(selected && DIRECT_CUSTOM_REFERENCE_ENGINE_IDS.includes(selected.manifest.engine_id as (typeof DIRECT_CUSTOM_REFERENCE_ENGINE_IDS)[number]));
	const supportsEmotion = activeParamKeys.has('emotion');
	const isOmniVoice = state.engineId === 'omnivoice';
	const isMimoPreset = state.engineId === 'mimo-v2.5-tts-preset';
	const useCustomReference = acceptsDirectCustomReference && state.voiceSource === 'reference_audio';
	const useLibraryReference =
		usesReferenceVoice && state.voiceSource === 'voice_library' && Boolean(state.voiceId);
	const isQwen3TTS = state.engineId === 'qwen3-tts-mlx-0.6b';
	const qwen3ReferenceRoute = isQwen3TTS && (useCustomReference || useLibraryReference);
	const qwen3VoiceDesignRoute =
		isQwen3TTS && !qwen3ReferenceRoute && Boolean(state.voiceDesignPrompt.trim());
	const qwen3PresetRoute = isQwen3TTS && !qwen3ReferenceRoute && !qwen3VoiceDesignRoute;
	const fallbackEmotionMode: GenerateRequest['emotion_mode'] =
		supportsEmotion && Boolean(state.emotion) ? 'emotion_vector' : 'follow_reference';
	const indexEmotionState =
		(state.engineUiStateById[INDEX_TTS_ENGINE_ID] as IndexTtsEmotionState | undefined) ??
		createDefaultIndexTtsEmotionState(state.emoAlpha);
	const indexEmotionRequest = indexTtsEmotionStateToRequest(
		indexEmotionState,
		fallbackEmotionMode,
		state.emoAlpha
	);
	const indexEmotionReferenceParameters =
		state.engineId === INDEX_TTS_ENGINE_ID && indexEmotionState.enabled
			? {
					emotion_reference_voice_id: indexEmotionRequest.emotion_reference_voice_id,
					emotion_reference_audio_path: indexEmotionRequest.emotion_reference_audio_path,
					emotion_reference_source_audio_path: indexEmotionRequest.emotion_reference_source_audio_path,
					emotion_reference_source_duration_ms: indexEmotionRequest.emotion_reference_source_duration_ms,
					emotion_reference_trim_start_ms: indexEmotionRequest.emotion_reference_trim_start_ms,
					emotion_reference_trim_end_ms: indexEmotionRequest.emotion_reference_trim_end_ms
				}
			: {};

	return {
		text: state.text,
		engine_id: state.engineId,
		engine_parameters: state.engineParameters,
		source: state.requestSource || null,
		project_id: state.requestProjectId || null,
		segment_id: state.requestSegmentId || null,
		localized_subtitle_id: state.requestLocalizedSubtitleId || null,
		cue_id: state.requestCueId || null,
		bind_to_video_localization:
			state.requestSource === 'video_localization' && state.requestBindToVideoLocalization,
		voice_id: useLibraryReference ? state.voiceId || null : null,
		voice_source: usesReferenceVoice ? (useCustomReference ? 'reference_audio' : 'voice_library') : undefined,
		reference_audio_path: useCustomReference ? state.customVoiceReferenceAudioPath || null : null,
		reference_audio_license_status: useCustomReference ? state.requestReferenceLicenseStatus ?? 'self_voice' : null,
		reference_audio_tags: useCustomReference ? (state.requestReferenceTags.length ? state.requestReferenceTags : ['custom-reference']) : [],
		ref_text:
			useCustomReference && state.customVoiceTranscript.trim()
				? state.customVoiceTranscript.trim()
			: useLibraryReference && selectedVoice?.reference_text.trim()
			? selectedVoice.reference_text.trim()
			: null,
		custom_reference_source_audio_path: useCustomReference ? state.customVoiceSourceAudioPath || null : null,
		custom_reference_source_duration_ms: useCustomReference ? state.customVoiceSourceDurationMs : null,
		custom_reference_trim_start_ms: useCustomReference ? state.customVoiceTrimStartMs : null,
		custom_reference_trim_end_ms: useCustomReference ? state.customVoiceTrimEndMs : null,
		language: state.language,
		emotion_mode: state.engineId === INDEX_TTS_ENGINE_ID ? indexEmotionRequest.emotion_mode : fallbackEmotionMode,
		emotion: state.engineId === INDEX_TTS_ENGINE_ID && indexEmotionRequest.emotion_mode === 'emotion_reference' ? null : (supportsEmotion && Boolean(state.emotion) ? state.emotion : null),
		emotion_text: isOmniVoice && !state.voiceId && !useCustomReference ? state.voiceDesign : null,
		...indexEmotionReferenceParameters,
		style_instruction:
			activeParamKeys.has('style_instruction') && !(isQwen3TTS && !qwen3PresetRoute)
				? state.styleInstruction || null
				: null,
		voice_design_prompt:
			activeParamKeys.has('voice_design_prompt') && !(isQwen3TTS && qwen3ReferenceRoute)
				? state.voiceDesignPrompt || null
				: null,
		optimize_text_preview: activeParamKeys.has('optimize_text_preview') ? state.optimizeTextPreview : false,
		mimo_voice: isMimoPreset ? state.mimoVoice : null,
		speaker_id:
			activeParamKeys.has('speaker_id') && !(isQwen3TTS && !qwen3PresetRoute)
				? state.speakerId || null
				: null,
		prompt: activeParamKeys.has('prompt') ? state.voicePrompt || null : null,
		nfe_step: state.nfeStep,
		cfg_strength: state.cfgStrength,
		target_rms: state.targetRms,
		cross_fade_duration: state.crossFadeDuration,
		sway_sampling_coef: state.swaySamplingCoef,
		fix_duration: state.fixDuration,
		remove_silence: state.removeSilence,
		emo_alpha: state.engineId === INDEX_TTS_ENGINE_ID ? indexEmotionRequest.emo_alpha : state.emoAlpha,
		speed: state.speed,
		pitch_rate: activeParamKeys.has('pitch_rate') ? state.pitchRate : undefined,
		sample_rate: activeParamKeys.has('sample_rate') ? state.doubaoSampleRate : undefined,
		bit_rate: activeParamKeys.has('bit_rate') ? state.doubaoBitRate : undefined,
		loudness_rate: activeParamKeys.has('loudness_rate') ? state.doubaoLoudnessRate : undefined,
		enable_subtitle: activeParamKeys.has('enable_subtitle') ? state.doubaoEnableSubtitle : false,
		silence_duration: activeParamKeys.has('silence_duration') ? state.doubaoSilenceDuration : 0,
		aigc_watermark: activeParamKeys.has('aigc_watermark') ? state.doubaoAigcWatermark : false,
		temperature: state.temperature,
		top_p: state.topP,
		top_k: state.topK,
		repetition_penalty: state.repetitionPenalty,
		seed: state.seed,
		max_mel_tokens: state.maxMelTokens,
		max_text_tokens_per_segment: state.maxTextTokensPerSegment,
		interval_silence: state.intervalSilence,
		segment_overlap_ms: 50,
		diffusion_steps: state.diffusionSteps,
		cfg_rate: state.cfgRate,
		guidance_scale: state.guidanceScale,
		duration: state.duration,
		audio_chunk_duration: state.audioChunkDuration,
		audio_chunk_threshold: state.audioChunkThreshold,
		max_tokens: state.maxTokens,
		cfg_scale: state.cfgScale,
		ddpm_steps: state.ddpmSteps,
		output_format: state.outputFormat
	};
}

function applyRequest(
	state: GenerateStoreState,
	req: GenerateRequest,
	options: { preserveVideoLocalizationBinding?: boolean } = {}
): Partial<GenerateStoreState> {
	const engineDefaults = getEngineDefaults(state, req.engine_id);
	const isMimoEngineRequest = isMimoEngine(req.engine_id);
	const restoredIndexEmotion = indexTtsEmotionStateFromRequest(req);

	return {
		...engineDefaults,
		// Old tasks and hand-written presets often omit controls added later.
		// Preserve the current engine default instead of showing an incomplete UI.
		engineParameters: { ...engineDefaults.engineParameters, ...(req.engine_parameters ?? {}) },
		engineUiStateById: req.engine_id === INDEX_TTS_ENGINE_ID
			? { ...state.engineUiStateById, [INDEX_TTS_ENGINE_ID]: restoredIndexEmotion }
			: state.engineUiStateById,
		text: req.text,
		requestSource: req.source ?? '',
		requestProjectId: req.project_id ?? '',
		requestSegmentId: req.segment_id ?? '',
		requestLocalizedSubtitleId: req.localized_subtitle_id ?? '',
		requestCueId: req.cue_id ?? '',
		requestBindToVideoLocalization:
			options.preserveVideoLocalizationBinding !== false && Boolean(req.bind_to_video_localization),
		requestReferenceLicenseStatus: req.reference_audio_license_status ?? null,
		requestReferenceTags: [...(req.reference_audio_tags ?? [])],
		voiceSource: req.reference_audio_path ? 'reference_audio' : 'voice_library',
		voiceId: req.voice_id ?? '',
		customVoiceFileName: req.reference_audio_path ? req.reference_audio_path.split('/').pop() ?? '自定义参考音频' : '',
		customVoiceFileId: '',
		customVoicePreviewUrl: '',
		customVoiceReferenceAudioPath: req.reference_audio_path ?? '',
		customVoiceSourceFileId: '',
		customVoiceSourceAudioPath: req.custom_reference_source_audio_path ?? '',
		customVoiceSourceDurationMs: req.custom_reference_source_duration_ms ?? null,
		customVoiceTrimStartMs: req.custom_reference_trim_start_ms ?? null,
		customVoiceTrimEndMs: req.custom_reference_trim_end_ms ?? null,
		customVoiceTranscript: req.reference_audio_path ? req.ref_text ?? '' : '',
		customVoiceSrt: '',
		customVoiceDurationMs: null,
		customVoiceSrtSegmentCount: 0,
		customVoiceTranscriptionId: '',
		customVoiceConfirmed: Boolean(req.reference_audio_path && (req.ref_text ?? '').trim()),
		customVoiceBusy: false,
		customVoiceError: '',
		customVoiceQualityWarnings: [],
		language: req.language || 'zh',
		emotion:
			req.emotion_mode === 'emotion_vector' && typeof req.emotion === 'string' ? req.emotion : '',
		voiceDesign:
			req.emotion_mode === 'emotion_text' && typeof req.emotion_text === 'string'
				? req.emotion_text
				: DEFAULT_VOICE_DESIGN,
		voiceDesignPrompt: req.voice_design_prompt ?? engineDefaults.voiceDesignPrompt,
		optimizeTextPreview: req.optimize_text_preview ?? false,
		styleInstruction: req.style_instruction || '',
		mimoVoice: req.mimo_voice || 'mimo_default',
		speakerId: req.speaker_id || '',
		voicePrompt: req.prompt || '',
		nfeStep: req.nfe_step ?? F5_DEFAULTS.nfeStep,
		cfgStrength: req.cfg_strength ?? F5_DEFAULTS.cfgStrength,
		targetRms: req.target_rms ?? F5_DEFAULTS.targetRms,
		crossFadeDuration: req.cross_fade_duration ?? F5_DEFAULTS.crossFadeDuration,
		swaySamplingCoef: req.sway_sampling_coef ?? F5_DEFAULTS.swaySamplingCoef,
		fixDuration: req.fix_duration ?? F5_DEFAULTS.fixDuration,
		removeSilence: req.remove_silence ?? F5_DEFAULTS.removeSilence,
		emoAlpha: req.emo_alpha ?? INDEX_TTS_DEFAULTS.emoAlpha,
		speed: req.speed ?? INDEX_TTS_DEFAULTS.speed,
		pitchRate: req.pitch_rate ?? engineDefaults.pitchRate,
		doubaoSampleRate: (req.sample_rate ?? engineDefaults.doubaoSampleRate) as GenerateStoreState['doubaoSampleRate'],
		doubaoBitRate: req.bit_rate ?? engineDefaults.doubaoBitRate,
		doubaoLoudnessRate: req.loudness_rate ?? engineDefaults.doubaoLoudnessRate,
		doubaoEnableSubtitle: req.enable_subtitle ?? engineDefaults.doubaoEnableSubtitle,
		doubaoSilenceDuration: req.silence_duration ?? engineDefaults.doubaoSilenceDuration,
		doubaoAigcWatermark: req.aigc_watermark ?? engineDefaults.doubaoAigcWatermark,
		temperature: req.temperature ?? engineDefaults.temperature,
		topP: req.top_p ?? engineDefaults.topP,
		topK: req.top_k ?? engineDefaults.topK,
		maxTextTokensPerSegment:
			req.max_text_tokens_per_segment ?? INDEX_TTS_DEFAULTS.maxTextTokensPerSegment,
		intervalSilence: req.interval_silence ?? INDEX_TTS_DEFAULTS.intervalSilence,
		diffusionSteps: req.diffusion_steps ?? engineDefaults.diffusionSteps,
		cfgRate: req.cfg_rate ?? engineDefaults.cfgRate,
		guidanceScale: req.guidance_scale ?? OMNIVOICE_DEFAULTS.guidanceScale,
		duration: req.duration ?? OMNIVOICE_DEFAULTS.duration,
		audioChunkDuration: req.audio_chunk_duration ?? OMNIVOICE_DEFAULTS.audioChunkDuration,
		audioChunkThreshold: req.audio_chunk_threshold ?? OMNIVOICE_DEFAULTS.audioChunkThreshold,
		maxTokens: req.max_tokens ?? engineDefaults.maxTokens,
		cfgScale: req.cfg_scale ?? engineDefaults.cfgScale,
		ddpmSteps: req.ddpm_steps ?? engineDefaults.ddpmSteps,
		maxMelTokens: req.max_mel_tokens ?? INDEX_TTS_DEFAULTS.maxMelTokens,
		repetitionPenalty: req.repetition_penalty ?? engineDefaults.repetitionPenalty,
		seed: req.seed ?? engineDefaults.seed ?? null,
		outputFormat: req.output_format ?? INDEX_TTS_DEFAULTS.outputFormat,
		showAdvanced: req.engine_id === 'f5-tts',
		showMoreParams: req.emotion_mode === 'emotion_reference' ? true : state.showMoreParams
	};
}

export function createGenerateStore() {
	const store = writable<GenerateStoreState>(createInitialState());

	return {
		subscribe: store.subscribe,
		set: store.set,
		update: store.update,
		setEngine(engineId: string) {
			store.update((state) => ({
				...state,
				...getEngineDefaults(state, engineId)
			}));
		},
		toRequest() {
			return createRequest(get(store));
		},
		fromRequest(req: GenerateRequest, options: { preserveVideoLocalizationBinding?: boolean } = {}) {
			store.update((state) => ({
				...state,
				...applyRequest(state, req, options)
			}));
		},
		reset() {
			store.set(createInitialState());
		}
	};
}

export const generateStore = createGenerateStore();
